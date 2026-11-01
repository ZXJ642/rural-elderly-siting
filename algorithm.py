# -*- coding: utf-8 -*-
"""
农村养老医疗服务点选址与均衡划片包干（v2）
QGIS Processing 脚本 —— 调用本地 PyPy2 后端 EDP 启发式求解器

v2 修改点（按需求文档）：
  1. 需求位置：不再用需求面质心，改为通过 site_id 连接需求点图层，直接使用需求点坐标。
  2. 医疗点定位：不再"找最近质心"，改为用空间包含判断医疗点坐落在哪个需求面上。
  3. 方向 a：保持只传需求面作为计算单元；被医疗点标记为"可作中心"的需求面，
     其坐标改用医疗点坐标（不用需求点坐标、不用面质心）。
  4. 文案：行政村/村 → 需求面/需求点。
"""
import os
import csv
import glob
import shutil
import subprocess
import tempfile

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProcessingParameterFeatureSink,
    QgsFeatureRequest,
    QgsGeometry,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsFeatureSink,
    QgsPointXY,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsFillSymbol,
    QgsSpatialIndex,
    QgsProcessingException,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QMessageBox, QFileDialog


_KEYBOARD_STUB = '''# -*- coding: utf-8 -*-
# 自动生成的 keyboard 空存根：让后端 PyPy2 import keyboard 通过，
# 不真正监听键盘。所有调用均为 no-op。
class _Any(object):
    def __call__(self, *a, **k): return None
    def __getattr__(self, name): return self
    def __setitem__(self, *a, **k): pass
    def __getitem__(self, *a, **k): return None

def on_press_key(key, cb=None, suppress=False, **kw): return _Any()
def on_release_key(key, cb=None, suppress=False, **kw): return _Any()
def hook(cb, **kw): return _Any()
def wait(key=None, **kw): return None
def is_pressed(key): return False
def press(key): return None
def release(key): return None
def tap(key): return None
def write(text, **kw): return None
def read_event(): return None
def start_recording(**kw): return []
def stop_recording(): pass
class KeyboardEvent(object): pass
'''


class RuralElderlySitingAlgorithm(QgsProcessingAlgorithm):
    INPUT_POLY = "INPUT_POLY"
    SITE_ID_FIELD = "SITE_ID_FIELD"
    DEMAND_FIELD = "DEMAND_FIELD"
    INPUT_DEMAND_PT = "INPUT_DEMAND_PT"
    DEMAND_PT_SITEID = "DEMAND_PT_SITEID"
    INPUT_POINTS = "INPUT_POINTS"
    POINT_NAME_FIELD = "POINT_NAME_FIELD"
    NUM_FACILITIES = "NUM_FACILITIES"
    DEVIATION = "DEVIATION"
    PSIZE = "PSIZE"
    MAX_LOOPS = "MAX_LOOPS"
    PYPATH = "PYPATH"
    SCRIPT_PATH = "SCRIPT_PATH"
    OUTPUT_POLY = "OUTPUT_POLY"
    OUTPUT_CENTER = "OUTPUT_CENTER"
    OUTPUT_AREA = "OUTPUT_AREA"

    PALETTE = [
        "#e6194B", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
        "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
        "#469990", "#dcbeff", "#9A6324", "#800000", "#aaffc3",
        "#808000", "#ffd8b1", "#000075", "#a9a9a9", "#7f7f7f",
    ]

    def name(self):
        return "rural_elderly_siting"

    def displayName(self):
        return "农村养老医疗点选址+均衡划片(v2)"

    def group(self):
        return "农村养老规划"

    def groupId(self):
        return "rural_elderly"

    def shortHelpString(self):
        return (
            "输入：需求面图层（含老年人口字段）+ 需求点图层（按 site_id 与需求面对应，提供需求点坐标）"
            "+ 候选服务点图层（村卫生室/乡镇卫生院）。\n"
            "需求坐标直接使用需求点坐标（非面质心）；医疗点按空间包含判断其坐落在哪个需求面上；"
            "被选中为中心的需求面，坐标改用医疗点坐标。\n"
            "输出：每个需求面被划入哪个片区、服务中心位置、平均距离、人口偏差。\n"
            "后端：调用本地 PyPy2 运行 SSCFLP/EDP 启发式求解器。"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_POLY, "需求面图层（多边形）",
            types=[QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterField(
            self.SITE_ID_FIELD, "需求面 site_id 字段（文本/唯一标识）",
            parentLayerParameterName=self.INPUT_POLY,
            type=QgsProcessingParameterField.Any))
        self.addParameter(QgsProcessingParameterField(
            self.DEMAND_FIELD, "老年人口字段",
            parentLayerParameterName=self.INPUT_POLY,
            type=QgsProcessingParameterField.Any))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_DEMAND_PT, "需求点图层（按 site_id 连接，提供需求点坐标）",
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.DEMAND_PT_SITEID, "需求点 site_id 字段（与需求面对应）",
            parentLayerParameterName=self.INPUT_DEMAND_PT,
            type=QgsProcessingParameterField.Any))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_POINTS, "候选服务点图层（村卫生室/卫生院）",
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.POINT_NAME_FIELD, "服务点名称字段（可选）",
            parentLayerParameterName=self.INPUT_POINTS, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.NUM_FACILITIES, "服务点数量 k",
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=10, minValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.DEVIATION, "人口偏差上限（0.10 = ±10%）",
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.10, minValue=0.01))
        self.addParameter(QgsProcessingParameterNumber(
            self.PSIZE, "多起点群大小",
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=3, minValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_LOOPS, "无改进停止轮数",
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=50, minValue=10))
        self.addParameter(QgsProcessingParameterString(
            self.PYPATH, "PyPy2 解释器路径",
            defaultValue=r"D:\pypy2.7-v7.3.17-win64\pypy2.exe"))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_POLY, "划片结果面图层",
            type=QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_CENTER, "选中医疗设施点图层",
            type=QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_AREA, "服务片区面图层（按中心合并）",
            type=QgsProcessing.TypeVectorPolygon))

    def processAlgorithm(self, parameters, context, feedback):
        poly_layer = self.parameterAsVectorLayer(parameters, self.INPUT_POLY, context)
        site_id_field = self.parameterAsString(parameters, self.SITE_ID_FIELD, context)
        demand_field = self.parameterAsString(parameters, self.DEMAND_FIELD, context)
        req_pt_layer = self.parameterAsVectorLayer(parameters, self.INPUT_DEMAND_PT, context)
        req_pt_siteid = self.parameterAsString(parameters, self.DEMAND_PT_SITEID, context)
        point_layer = self.parameterAsVectorLayer(parameters, self.INPUT_POINTS, context)
        point_name_field = self.parameterAsString(parameters, self.POINT_NAME_FIELD, context)
        k = self.parameterAsInt(parameters, self.NUM_FACILITIES, context)
        dev = self.parameterAsDouble(parameters, self.DEVIATION, context)
        psize = self.parameterAsInt(parameters, self.PSIZE, context)
        maxloops = self.parameterAsInt(parameters, self.MAX_LOOPS, context)
        pypath = self.parameterAsString(parameters, self.PYPATH, context)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "backend", "edp_ils31.py")

        # ---------- 0. 探测 PyPy2 ----------
        pypath = self._resolve_pypy(pypath, feedback)

        # ---------- 1. 坐标系 ----------
        src_crs = poly_layer.crs()
        target_crs = self._pick_projected_crs(src_crs, poly_layer)
        feedback.pushInfo(f"[1/8] 源 CRS: {src_crs.authid()}  →  投影 CRS: {target_crs.authid()}")

        poly_xformer = QgsCoordinateTransform(src_crs, target_crs, QgsProject.instance())
        req_pt_xformer = QgsCoordinateTransform(req_pt_layer.crs(), target_crs, QgsProject.instance())
        pt_xformer = QgsCoordinateTransform(point_layer.crs(), target_crs, QgsProject.instance())

        # ---------- 2. 读取需求点，建立 site_id -> (x_proj, y_proj) ----------
        feedback.pushInfo("[2/8] 读取需求点坐标（按 site_id 连接）...")
        req_pt_map = {}
        for pf in req_pt_layer.getFeatures():
            sid = self._norm_id(pf[req_pt_siteid])
            if sid is None:
                continue
            g = QgsGeometry(pf.geometry())
            g.transform(req_pt_xformer)
            p = g.asPoint()
            req_pt_map[sid] = (p.x(), p.y())
        feedback.pushInfo(f"      需求点 {len(req_pt_map)} 个已建立 site_id 坐标映射")

        # ---------- 3. 读取需求面；坐标优先用需求点坐标（不用质心） ----------
        feedback.pushInfo("[3/8] 读取需求面（坐标取需求点，不再取质心）...")
        units = []
        geoms_p = []
        miss_pt = []
        for feat in poly_layer.getFeatures():
            g = QgsGeometry(feat.geometry())
            g.transform(poly_xformer)
            sid = self._norm_id(feat[site_id_field])
            try:
                demand = float(feat[demand_field] or 0)
            except Exception:
                demand = 0.0
            if sid is not None and sid in req_pt_map:
                x, y = req_pt_map[sid]
            else:
                c = g.centroid().asPoint()
                x, y = c.x(), c.y()
                miss_pt.append(sid if sid else "<空>")
            units.append({
                "fid": feat.id(),
                "orig_id": feat.id(),
                "sid": sid,
                "demand": demand,
                "x": x, "y": y,
                # 保留原始需求点坐标；后续 x/y 会被改为医疗点坐标（方向a）
                "req_x": x, "req_y": y,
                "is_candidate": False,
                "name": "",
            })
            geoms_p.append(g)
        n = len(units)
        total_old = sum(u["demand"] for u in units)
        feedback.pushInfo(f"      {n} 个需求面, 老年人口合计 {total_old:.0f}")
        if miss_pt:
            feedback.reportError(
                f"警告: {len(miss_pt)} 个需求面未匹配到需求点，已退回面质心：{miss_pt[:5]}")
        if n < k:
            raise QgsProcessingException(f"需求面数 {n} < k={k}")

        # ---------- 4. 候选服务点：用空间包含判断坐落在哪个需求面 ----------
        feedback.pushInfo("[4/8] 候选服务点挂载（按位置包含，不再用最近质心）...")
        sindex = QgsSpatialIndex()
        for i, ug in enumerate(geoms_p):
            f = QgsFeature()
            f.setGeometry(ug)
            f.setId(i)
            sindex.addFeature(f)

        def _host_polygon(pt_geom):
            """返回包含该点的需求面下标；找不到则退化到最近需求面下标。
            退化规则：若医疗点没有坐落在任何需求面上，则选用与其最近的需求面
            邻接——"最近"= 医疗点XY 与 需求点XY 的距离最近。"""
            box = pt_geom.boundingBox()
            cand = sindex.intersects(box)
            for idx in cand:
                if geoms_p[idx].contains(pt_geom):
                    return idx
            # 退化：最近需求面（按"医疗点XY 与 需求点XY"距离最近）
            px, py = pt_geom.asPoint().x(), pt_geom.asPoint().y()
            best = 0
            best_d = 1e30
            for i, u in enumerate(units):
                d = (u["req_x"] - px) ** 2 + (u["req_y"] - py) ** 2
                if d < best_d:
                    best_d = d
                    best = i
            return best

        ncand = 0
        for pf in point_layer.getFeatures():
            pg = QgsGeometry(pf.geometry())
            pg.transform(pt_xformer)
            host = _host_polygon(pg)
            u = units[host]
            u["is_candidate"] = True
            # 方向 a：被标记为可作中心的需求面，坐标改用医疗点坐标
            u["x"] = pg.asPoint().x()
            u["y"] = pg.asPoint().y()
            if point_name_field:
                u["name"] = str(pf[point_name_field] or "")
            ncand += 1
        feedback.pushInfo(f"      候选服务点 {point_layer.featureCount()} 个 → {ncand} 个候选需求面")
        ncand_face = sum(1 for u in units if u["is_candidate"])
        feedback.pushInfo(f"      实际被标记为候选中心的需求面 {ncand_face} 个")
        if ncand_face < k:
            raise QgsProcessingException(
                f"候选需求面数 {ncand_face} < k={k}，请检查服务点是否落在需求面范围内")

        # ---------- 5. 邻接关系（需求面共边） ----------
        feedback.pushInfo("[5/8] 计算需求面共边邻接...")
        adj = set()
        engines = [QgsGeometry.createGeometryEngine(g.constGet()) for g in geoms_p]
        for e in engines:
            e.prepareGeometry()
        for i in range(n):
            if feedback.isCanceled():
                raise QgsProcessingException("用户取消")
            if i % 200 == 0:
                feedback.setProgress(int(40 * i / max(n, 1)))
            bi = geoms_p[i].boundingBox()
            for j in range(i + 1, n):
                if not bi.intersects(geoms_p[j].boundingBox()):
                    continue
                if engines[i].intersects(geoms_p[j].constGet()):
                    if not engines[i].contains(geoms_p[j].constGet()) and \
                       not engines[j].contains(geoms_p[i].constGet()):
                        adj.add((i, j))
        feedback.pushInfo(f"      {len(adj)} 条邻接边")

        # ---------- 6. 落盘 ----------
        feedback.pushInfo("[6/8] 写入临时文件并调用 PyPy 后端...")
        tmpdir = tempfile.mkdtemp(prefix="edp_")
        units_txt = os.path.join(tmpdir, "units.txt")
        conn_txt = os.path.join(tmpdir, "conn.txt")
        with open(units_txt, "w") as f:
            f.write("ID\tDemand\tx\ty\tFcand\tFcost\tFcap\n")
            for i, u in enumerate(units):
                fcap = 99999 if u["is_candidate"] else 0
                f.write(f"{i}\t{int(u['demand'])}\t{u['x']:.3f}\t{u['y']:.3f}\t"
                        f"{1 if u['is_candidate'] else 0}\t0\t{fcap}\n")
        with open(conn_txt, "w") as f:
            f.write('"OID_","IN_FID","NEAR_FID","NEAR_DIST"\n')
            for a, b in adj:
                f.write(f",{a},{b},0.000000\n")

        # 打包 keyboard 空存根：后端 import keyboard 即可通过，无需用户手动安装
        keyboard_stub = os.path.join(tmpdir, "keyboard.py")
        with open(keyboard_stub, "w") as f:
            f.write(_KEYBOARD_STUB)

        # ---------- 7. 调后端 ----------
        cmd = [pypath, script, units_txt, conn_txt,
               str(k), f"{dev:.3f}", str(psize), str(maxloops)]
        feedback.pushInfo("      " + " ".join(cmd))
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        old_pypath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = tmpdir + (os.pathsep + old_pypath if old_pypath else "")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=os.path.dirname(script),
            env=env,
        )
        tail = []
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            txt = line.decode("utf-8", errors="replace").rstrip()
            tail.append(txt)
            if len(tail) > 300:
                tail.pop(0)
            if txt.startswith("Loop") or txt.startswith("ILS") or txt.startswith("initial"):
                feedback.pushConsoleInfo(txt)
            if feedback.isCanceled():
                proc.kill()
                raise QgsProcessingException("用户取消")
        proc.wait()
        if proc.returncode != 0:
            feedback.reportError("后端日志（最后 300 行）:\n" + "\n".join(tail))
            raise QgsProcessingException(f"后端退出码 {proc.returncode}")

        # ---------- 8. 读结果 ----------
        result_txt = units_txt + f"_equal_workload_{k}.txt"
        if not os.path.exists(result_txt):
            raise QgsProcessingException(f"结果文件不存在: {result_txt}")
        feedback.pushInfo("[7/8] 读取后端结果...")
        unit_district = {}
        center_info = {}
        with open(result_txt, "r") as f:
            rdr = csv.reader(f, delimiter="\t")
            next(rdr)
            for row in rdr:
                if len(row) < 10:
                    continue
                uid = int(row[0])
                cid = int(row[7])
                dist = float(row[8])
                covered = int(row[9])
                unit_district[uid] = (cid, dist, covered)
                center_info.setdefault(cid, {"covered": covered, "units": []})
                center_info[cid]["units"].append(uid)

        feedback.pushInfo(f"      选出 {len(center_info)} 个服务点:")
        for cid, info in sorted(center_info.items()):
            feedback.pushInfo(f"        中心#{cid}: {len(info['units'])} 需求面, {info['covered']:.0f} 老人")

        # ---------- 9. 写输出图层（三输出：分配面 / 选中设施点 / 片区合并面） ----------
        feedback.pushInfo("[8/8] 写回三个输出图层并配色...")

        center_order = sorted(center_info.keys())
        center_to_no = {c: i + 1 for i, c in enumerate(center_order)}

        # 预统计每个中心：服务面数、服务人口、平均/最大距离
        center_stats = {}
        for cid, info in center_info.items():
            us = info["units"]
            pops = [units[i]["demand"] for i in us]
            dists = [unit_district[i][1] for i in us]
            center_stats[cid] = {
                "n_units": len(us),
                "total_pop": sum(pops),
                "avg_dist": (sum(dists) / len(dists)) if dists else 0.0,
                "max_dist": max(dists) if dists else 0.0,
                "name": units[cid]["name"],
            }

        # (1) 划片结果面：每个需求面一行
        f1 = QgsFields()
        f1.append(QgsField("district_id", QVariant.Int))
        f1.append(QgsField("center_fid", QVariant.LongLong))
        f1.append(QgsField("center_name", QVariant.String))
        f1.append(QgsField("dist_km", QVariant.Double))
        f1.append(QgsField("covered_pop", QVariant.LongLong))
        f1.append(QgsField("demand", QVariant.Double))
        (sink1, id1) = self.parameterAsSink(
            parameters, self.OUTPUT_POLY, context,
            f1, QgsWkbTypes.Polygon, target_crs)
        for feat in poly_layer.getFeatures():
            i = next((idx for idx, u in enumerate(units) if u["fid"] == feat.id()), None)
            if i is None:
                continue
            cid, dist, covered = unit_district.get(i, (-1, 0.0, 0))
            nf = QgsFeature(f1)
            nf.setGeometry(geoms_p[i])
            nf.setAttribute("district_id", center_to_no.get(cid, 0))
            nf.setAttribute("center_fid", cid)
            nf.setAttribute("center_name", units[cid]["name"] if cid in units else "")
            nf.setAttribute("dist_km", round(dist, 3))
            nf.setAttribute("covered_pop", covered)
            nf.setAttribute("demand", units[i]["demand"])
            sink1.addFeature(nf, QgsFeatureSink.FastInsert)

        # (2) 选中医疗设施点：每个中心一个点（坐标即医疗点坐标，方向a）
        f2 = QgsFields()
        f2.append(QgsField("district_id", QVariant.Int))
        f2.append(QgsField("center_fid", QVariant.LongLong))
        f2.append(QgsField("center_name", QVariant.String))
        f2.append(QgsField("n_units", QVariant.Int))
        f2.append(QgsField("total_pop", QVariant.Double))
        f2.append(QgsField("avg_dist_km", QVariant.Double))
        f2.append(QgsField("max_dist_km", QVariant.Double))
        (sink2, id2) = self.parameterAsSink(
            parameters, self.OUTPUT_CENTER, context,
            f2, QgsWkbTypes.Point, target_crs)
        for cid in center_order:
            st = center_stats[cid]
            nf = QgsFeature(f2)
            nf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(units[cid]["x"], units[cid]["y"])))
            nf.setAttribute("district_id", center_to_no[cid])
            nf.setAttribute("center_fid", cid)
            nf.setAttribute("center_name", st["name"])
            nf.setAttribute("n_units", st["n_units"])
            nf.setAttribute("total_pop", round(st["total_pop"], 1))
            nf.setAttribute("avg_dist_km", round(st["avg_dist"], 3))
            nf.setAttribute("max_dist_km", round(st["max_dist"], 3))
            sink2.addFeature(nf, QgsFeatureSink.FastInsert)

        # (3) 服务片区面：每个中心合并其服务的所有需求面
        f3 = QgsFields()
        f3.append(QgsField("district_id", QVariant.Int))
        f3.append(QgsField("center_fid", QVariant.LongLong))
        f3.append(QgsField("center_name", QVariant.String))
        f3.append(QgsField("n_units", QVariant.Int))
        f3.append(QgsField("total_pop", QVariant.Double))
        f3.append(QgsField("avg_dist_km", QVariant.Double))
        f3.append(QgsField("max_dist_km", QVariant.Double))
        (sink3, id3) = self.parameterAsSink(
            parameters, self.OUTPUT_AREA, context,
            f3, QgsWkbTypes.Polygon, target_crs)
        for cid in center_order:
            st = center_stats[cid]
            geoms = [geoms_p[i] for i in center_info[cid]["units"]]
            merged = QgsGeometry.unaryUnion(geoms) if geoms else QgsGeometry()
            nf = QgsFeature(f3)
            nf.setGeometry(merged)
            nf.setAttribute("district_id", center_to_no[cid])
            nf.setAttribute("center_fid", cid)
            nf.setAttribute("center_name", st["name"])
            nf.setAttribute("n_units", st["n_units"])
            nf.setAttribute("total_pop", round(st["total_pop"], 1))
            nf.setAttribute("avg_dist_km", round(st["avg_dist"], 3))
            nf.setAttribute("max_dist_km", round(st["max_dist"], 3))
            sink3.addFeature(nf, QgsFeatureSink.FastInsert)

        # 分配面自动按片区配色
        out_layer = context.getMapLayer(id1)
        if out_layer is not None:
            cats = []
            for cid in center_order:
                no = center_to_no[cid]
                color = QColor(self.PALETTE[(no - 1) % len(self.PALETTE)])
                sym = QgsFillSymbol.createSimple({
                    "color": color.name(),
                    "outline_color": "#333333",
                    "outline_width": "0.5",
                })
                cats.append(QgsRendererCategory(no, sym, f"片区 {no}"))
            out_layer.setRenderer(QgsCategorizedSymbolRenderer("district_id", cats))
            out_layer.triggerRepaint()

        feedback.pushInfo("完成。共输出 3 个图层。")
        return {self.OUTPUT_POLY: id1, self.OUTPUT_CENTER: id2, self.OUTPUT_AREA: id3}

    def _resolve_pypy(self, pypath, feedback):
        """启动时探测 PyPy2 解释器；找不到则弹窗让用户选择/指定。"""
        # 1. 用户参数里填的路径
        if pypath and os.path.isfile(pypath):
            feedback.pushInfo(f"[0/8] 使用 PyPy2: {pypath}")
            return pypath

        # 2. 自动探测常见位置
        feedback.pushInfo("[0/8] 未在默认路径找到 PyPy2，正在自动探测...")
        candidates = []
        for pat in [
            r"D:\pypy2.7-*\pypy2.exe",
            r"C:\pypy2.7-*\pypy2.exe",
            r"D:\Program Files\pypy2.7-*\pypy2.exe",
            r"C:\Program Files\pypy2.7-*\pypy2.exe",
            os.path.expanduser(r"~\pypy2.7-*\pypy2.exe"),
        ]:
            candidates.extend(glob.glob(pat))
        which = shutil.which("pypy2") or shutil.which("pypy")
        if which:
            candidates.append(which)
        for c in candidates:
            if os.path.isfile(c):
                feedback.pushInfo(f"[0/8] 自动探测到 PyPy2: {c}")
                return c

        # 3. 都找不到 -> 弹窗询问
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("未找到 PyPy2")
        box.setText("未找到 PyPy2 解释器。")
        box.setInformativeText(
            "本算法后端需要 PyPy2 (Python 2.7) 才能运行。\n\n"
            "【是】= 我已安装，手动指定 pypy2.exe 位置\n"
            "【否】= 未安装，我去装完后再回来运行"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        ret = box.exec_()

        if ret == QMessageBox.No:
            QMessageBox.information(
                None, "请先安装 PyPy2",
                "请到 https://www.pypy.org/download.html 下载 Windows 64 位版本，\n"
                "解压后记住 pypy2.exe 的路径，然后重新运行本算法，\n"
                "在 PyPy2 路径栏填入，或点【是】手动选择。"
            )
            raise QgsProcessingException(
                "用户尚未安装 PyPy2。请安装后重新运行，或在参数栏填入 pypy2.exe 路径。")

        # 用户选了"是" -> 弹文件选择框
        chosen, _ = QFileDialog.getOpenFileName(
            None, "选择 pypy2.exe", "",
            "PyPy2 解释器 (pypy2.exe);;所有文件 (*.*)")
        if not chosen:
            raise QgsProcessingException("未选择 pypy2.exe，算法取消。")
        if not os.path.isfile(chosen):
            raise QgsProcessingException(f"所选文件不存在: {chosen}")
        feedback.pushInfo(f"[0/8] 使用手动指定的 PyPy2: {chosen}")
        return chosen

    def _norm_id(self, v):
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    def _pick_projected_crs(self, src_crs, layer):
        if not src_crs.isGeographic():
            return src_crs
        ext = layer.extent()
        lon = (ext.xMinimum() + ext.xMaximum()) / 2.0
        zone = int((lon + 180) / 6) + 1
        crs = QgsCoordinateReferenceSystem(f"EPSG:{32600 + zone}")
        return crs if crs.isValid() else src_crs

    def createInstance(self):
        return RuralElderlySitingAlgorithm()
