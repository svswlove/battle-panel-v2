# 桌面启动器（`launcher.py`）

**用途**：双击/命令行启动 8810，并打开默认浏览器；以后可用 PyInstaller 封装成 exe。
**边界**：只负责启动与打开页面，不含任何业务逻辑；不接 QMT、不下单、不改旧 8800/8002。

## 用法

```powershell
cd <panel-v2>
python launcher.py                 # 确保 8810 在跑，打开浏览器
python launcher.py --no-browser    # 只启动/检查，不开浏览器
python launcher.py --check         # 只检查 8810 健康，不启动
```

退出码：`0` 成功；`1` 启动超时或 8810 不可用。启动时用隐藏窗口调用 `run.ps1`；若已在运行则不重复启动。

## 以后封装 exe

建议用 PyInstaller `--onedir`（不要 onefile）：

```powershell
pyinstaller --noconsole --onedir --name panel-v2 launcher.py
```

- 启动器只管拉起 `run.ps1` + 开浏览器；`app/`、`docs/`、插件目录保持外部可替换。
- 不要把 `app/plugins/` 打进 exe；升级插件不应重新打包。
- 若要“无浏览器窗口”，再引入 `pywebview`（WebView2），仍复用同一套 HTML/插件，不重写 UI。
