## Sitechecker v1.0.1 — fix Windows desktop launch

### Исправлено
- Приложение «не открывалось» после установки: сервер Flask стартовал, но **окно WebView2 падало** (CLR `0xE0434352`) без сообщения.
- WebView2 user data перенесён в `%APPDATA%\Sitechecker\webview2` (записываемый каталог).
- Явный GUI `edgechromium`, убран `icon=` (ронял CLR в frozen-сборке).
- Если нативное окно недоступно — **fallback в системный браузер** + MessageBox с URL.

### Установка
1. Удалите старую версию (Параметры → Приложения), если была.
2. Установите **Sitechecker-Setup-1.0.1.exe**.
3. Запустите Sitechecker из меню «Пуск».

### Если снова не видно окна
- Проверьте `%APPDATA%\Sitechecker\errors.log`
- Установите [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) (Evergreen)
- При fallback откройте вручную `http://127.0.0.1:6001/` (порт может быть другим — см. лог)
