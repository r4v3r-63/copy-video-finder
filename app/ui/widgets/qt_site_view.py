"""Веб-просмотр в Linux; Windows использует WebView2 с поддержкой H.264."""

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from app.ui.widgets.site_player import allowed_page


class SitePage(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main_frame):
        return url.toString() == "about:blank" or allowed_page(url.toString())

    def createWindow(self, window_type):
        return self  # ссылки и вход остаются внутри панели


class QtSiteView(QWebEngineView):
    cookiesReady = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = QWebEngineProfile(self)  # без имени — только сеанс в памяти
        self._cookies = {}
        self.profile.cookieStore().cookieAdded.connect(self._cookie_added)
        self.profile.cookieStore().cookieRemoved.connect(self._cookie_removed)
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

    def _cookie_added(self, cookie):
        key = cookie.domain(), cookie.path(), bytes(cookie.name())
        self._cookies[key] = QNetworkCookie(cookie)

    def _cookie_removed(self, cookie):
        self._cookies.pop((cookie.domain(), cookie.path(), bytes(cookie.name())), None)

    def request_cookies(self, url: str, token):
        host = QUrl(url).host()
        items = [{"name": bytes(cookie.name()).decode("utf-8", "replace"),
                  "value": bytes(cookie.value()).decode("utf-8", "replace"),
                  "domain": cookie.domain() or host, "path": cookie.path(),
                  "secure": cookie.isSecure(), "http_only": cookie.isHttpOnly()}
                 for cookie in self._cookies.values()
                 if host == cookie.domain().lstrip(".") or host.endswith("." + cookie.domain().lstrip("."))]
        self.cookiesReady.emit(token, items)

    def navigate(self, url: str):
        self.setUrl(QUrl(url))

    def shutdown(self):
        self.stop()
        page = self.page()
        page.setAudioMuted(True)
        self.setPage(None)
        page.deleteLater()
