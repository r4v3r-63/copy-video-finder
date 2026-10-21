"""WebView2 в дочернем окне Qt. WinForms работает в своём STA-потоке и закрывается вместе с панелью."""

import logging
import os
import queue
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from app import paths
from app.ui.widgets.site_player import allowed_page


class WindowsSiteView(QWidget):
    loadFinished = pyqtSignal(bool)
    scriptFinished = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.commands = queue.Queue()
        self._parent_handle = int(self.winId())
        self._size = self._pixel_size()
        self._thread = None
        self._form = self._web = None
        self._url = "about:blank"
        self._closed = False
        self.private_mode = False
        self.failure = ""
        self._dll_directory = None
        root = paths.data_dir() / "webview"
        root.mkdir(exist_ok=True)
        self._cache = tempfile.mkdtemp(prefix="session-", dir=root)
        self._start()

    def _start(self):
        logger = logging.getLogger(__name__)
        if getattr(sys, "frozen", False):
            python_dll = Path(sys._MEIPASS) / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
            if python_dll.is_file():
                os.environ["PYTHONNET_PYDLL"] = str(python_dll)
        logger.info("WebView2: загрузка CLR")
        import clr
        import webview

        logger.info("WebView2: CLR загружен")

        libraries = Path(webview.__file__).parent / "lib"
        native = libraries / "runtimes" / "win-x64" / "native"
        self._dll_directory = os.add_dll_directory(str(native))
        os.environ["PATH"] += os.pathsep + str(native)
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        clr.AddReference(str(libraries / "Microsoft.Web.WebView2.Core.dll"))
        clr.AddReference(str(libraries / "Microsoft.Web.WebView2.WinForms.dll"))
        from System.Threading import ApartmentState, Thread, ThreadStart

        logger.info("WebView2: SDK подключён, запуск STA")

        self._thread = Thread(ThreadStart(self._run))
        self._thread.SetApartmentState(ApartmentState.STA)
        self._thread.IsBackground = True
        self._thread.Start()

    def _run(self):
        import ctypes
        from ctypes import wintypes

        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState
        from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2
        from System import Action, String
        from System.Drawing import Size
        from System.Threading.Tasks import Task
        from System.Windows.Forms import Application, DockStyle, Form, FormBorderStyle, Timer, UnhandledExceptionMode

        try:
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.ThrowException)
            form, web = Form(), WebView2()
            self._form, self._web = form, web
            form.FormBorderStyle = getattr(FormBorderStyle, "None")
            form.ShowInTaskbar = False
            form.TopLevel = False
            form.Size = Size(*self._size)
            web.Dock = DockStyle.Fill
            props = CoreWebView2CreationProperties()
            runtime = paths.app_dir() / "webview2"
            if runtime.is_dir():
                props.BrowserExecutableFolder = str(runtime)
            props.UserDataFolder = self._cache
            props.IsInPrivateModeEnabled = True
            web.CreationProperties = props
            form.Controls.Add(web)
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetParent.argtypes = (wintypes.HWND, wintypes.HWND)
            user32.SetParent.restype = wintypes.HWND
            hwnd = int(form.Handle.ToInt64())
            ctypes.set_last_error(0)
            user32.SetParent(hwnd, self._parent_handle)
            if ctypes.get_last_error():
                raise OSError("Не удалось встроить окно WebView2")

            def navigate(sender, args):
                if args.Uri != "about:blank" and not allowed_page(str(args.Uri)):
                    args.Cancel = True

            def popup(sender, args):
                args.Handled = True
                if allowed_page(str(args.Uri)):
                    web.CoreWebView2.Navigate(args.Uri)

            def permission(sender, args):
                args.State = CoreWebView2PermissionState.Deny

            def download(sender, args):
                args.Cancel = True

            def external(sender, args):
                args.Cancel = True

            def ready(sender, args):
                if not args.IsSuccess:
                    self.failure = f"Инициализация WebView2: код {args.InitializationException.HResult}"
                    logging.getLogger(__name__).error(self.failure)
                    self.loadFinished.emit(False)
                    return
                core = web.CoreWebView2
                self.private_mode = bool(core.Profile.IsInPrivateModeEnabled)
                logging.getLogger(__name__).info("WebView2: инициализация завершена, приватный сеанс %s",
                                                 self.private_mode)
                core.Settings.IsPasswordAutosaveEnabled = False
                core.Settings.IsGeneralAutofillEnabled = False
                core.Settings.IsWebMessageEnabled = False  # нет моста из сайта к Python
                core.Settings.AreDevToolsEnabled = False
                core.Settings.IsStatusBarEnabled = False
                core.IsMuted = True
                core.NavigationStarting += navigate
                core.NewWindowRequested += popup
                core.PermissionRequested += permission
                core.DownloadStarting += download
                if hasattr(core, "LaunchingExternalUriScheme"):
                    core.LaunchingExternalUriScheme += external
                core.NavigationCompleted += lambda sender, event: self.loadFinished.emit(bool(event.IsSuccess))
                core.Navigate(self._url)

            def tick(sender, args):
                while not self.commands.empty():
                    command, value = self.commands.get_nowait()
                    if command == "close":
                        timer.Stop()
                        web.Dispose()
                        form.Close()
                        Application.ExitThread()
                        return
                    if command == "resize":
                        form.Size = Size(*value)
                    elif command == "navigate":
                        self._url = value
                        if web.CoreWebView2:
                            web.CoreWebView2.Navigate(value)
                    elif command == "script" and web.CoreWebView2:
                        web.CoreWebView2.ExecuteScriptAsync(value).ContinueWith(
                            Action[Task[String]](lambda task: self.scriptFinished.emit(str(task.Result))))

            web.CoreWebView2InitializationCompleted += ready
            timer = Timer()
            timer.Interval = 50
            timer.Tick += tick
            timer.Start()
            form.Show()
            web.EnsureCoreWebView2Async(None)
            Application.Run()
            timer.Dispose()
            form.Dispose()
        except Exception as exc:  # noqa: BLE001 — ошибка native-компонента передаётся в Qt без адресов и данных сайта
            line = traceback.extract_tb(exc.__traceback__)[-1].lineno
            self.failure = f"{type(exc).__name__}, строка {line}, код {getattr(exc, 'HResult', 'не указан')}"
            logging.getLogger(__name__).error("WebView2: %s", self.failure)
            self.loadFinished.emit(False)
        finally:
            if self._web is not None:
                self._web.Dispose()
            if self._form is not None:
                self._form.Dispose()
            self._closed = True

    def _pixel_size(self):
        ratio = self.devicePixelRatioF()
        return max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio))

    def resizeEvent(self, event):
        self.commands.put(("resize", self._pixel_size()))
        super().resizeEvent(event)

    def navigate(self, url: str):
        self.commands.put(("navigate", url))

    def evaluate(self, script: str):
        self.commands.put(("script", script))

    def shutdown(self):
        if self._thread is not None and self._thread.IsAlive:
            self.commands.put(("close", None))
            self._thread.Join()  # UI-поток получает команду таймером; закрытие ждёт освобождения WebView2
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None
        shutil.rmtree(self._cache, ignore_errors=True)
