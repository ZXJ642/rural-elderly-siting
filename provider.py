from qgis.core import QgsProcessingProvider
from .algorithm import RuralElderlySitingAlgorithm


class RuralElderlyProvider(QgsProcessingProvider):
    def id(self):
        return "rural_elderly"

    def name(self):
        return "农村养老规划"

    def longName(self):
        return self.name()

    def icon(self):
        from qgis.PyQt.QtGui import QIcon
        return QIcon()

    def loadAlgorithms(self):
        self.addAlgorithm(RuralElderlySitingAlgorithm())
