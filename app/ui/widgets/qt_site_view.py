"""Веб-просмотр в Linux; Windows использует WebView2 с поддержкой H.264."""

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from app.ui.widgets.site_player import allowed_page


class SitePage(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main_frame):
        return url.toString() == "about:blank" or allowed_page(url.toString())

    def createWindow(self, window_type):
        return self  # ссылки и вход остаются внутри панели


class QtSiteView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = QWebEngineProfile(self)  # без имени — только сеанс в памяти
        self.profile.downloadRequested.connect(lambda item: item.cancel())
        page = SitePage(self.profile, self)
        page.setAudioMuted(True)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        page.settings().setUnknownUrlSchemePolicy(QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes)
        if hasattr(page, "permissionRequested"):
            page.permissionRequested.connect(lambda permission: permission.deny())
        else:
            page.featurePermissionRequested.connect(lambda origin, feature: page.setFeaturePermission(
                origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser))
        self.setPage(page)

    def navigate(self, url: str):
        self.setUrl(QUrl(url))

    def shutdown(self):
        self.stop()
        page = self.page()
        page.setAudioMuted(True)
        self.setPage(None)
        page.deleteLater()
