from qgis.core import QgsApplication
from .provider import RuralElderlyProvider


class RuralElderlySitingPlugin:
    def __init__(self, iface):
        self.provider = None

    def initProcessing(self):
        if self.provider is None:
            provider = RuralElderlyProvider()
            if not QgsApplication.processingRegistry().addProvider(provider):
                raise RuntimeError("无法注册农村养老规划 Processing Provider")
            self.provider = provider

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
