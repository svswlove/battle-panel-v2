using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace PanelShell
{
    /// <summary>
    /// 作战面板 v2 的独立窗口壳。
    ///
    /// 为什么不做成浏览器窗口（2026-11-13 定）：
    /// Chrome / Edge 的 --app 窗口跟主实例绑在一起，已有实例时新进程会把请求转发过去、
    /// 自己不产生窗口；只要浏览器里开着同名标签页，按标题找窗口就会认错。
    /// 这里用 WebView2 自建窗口，窗口归自己所有，跟浏览器开没开、开了什么标签页无关。
    ///
    /// 代价（已知）：WebView2 的用户数据目录独立于 Chrome，面板里存在 localStorage 的
    /// 界面偏好（K 线周期、展开状态、最近列表等）会从默认值重新开始一次。
    /// </summary>
    public class MainForm : Form
    {
        private const string PanelBase = "http://127.0.0.1:8810";
        private static readonly Color BgColor = Color.FromArgb(22, 24, 28);
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };

        private readonly WebView2 _web = new WebView2();
        private readonly AppSettings _settings = AppSettings.Load();
        private bool _panelLoaded;
        private bool _busy;
        private bool _initStarted;
        private bool _fatal;

        public MainForm()
        {
            Text = "作战面板 v2";
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            StartPosition = FormStartPosition.Manual;
            Bounds = _settings.Bounds();
            MinimumSize = new Size(1000, 700);
            BackColor = BgColor;
            Program.Log("MainForm 构造完成");

            _web.Dock = DockStyle.Fill;
            try { _web.DefaultBackgroundColor = BgColor; } catch { }
            Controls.Add(_web);

            // 用 Shown 而不是 Load：Load 在某些情况下会触发两次，
            // 第二次再建 WebView2 环境会直接报 0x800700AA（目录已被自己占住）。
            Shown += MainForm_Shown;
            FormClosing += (s, e) => _settings.SaveFrom(this);
        }

        private async void MainForm_Shown(object sender, EventArgs e)
        {
            if (_initStarted) return;
            _initStarted = true;
            await InitAsync();
        }

        // 第二次双击：广播消息命中这里，把自己叫到前台。
        protected override void WndProc(ref Message m)
        {
            if (Native.WM_PANELSHELL_ACTIVATE != 0 && m.Msg == (int)Native.WM_PANELSHELL_ACTIVATE)
            {
                Native.ForceForeground(Handle);
                return;
            }
            base.WndProc(ref m);
        }

        // F5 刷新；F12 开 DevTools（调试面板用，发布版也留着，不碍事）。
        protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
        {
            if (keyData == Keys.F5)
            {
                if (_panelLoaded && _web.CoreWebView2 != null) _web.CoreWebView2.Reload();
                return true;
            }
            if (keyData == Keys.F12)
            {
                if (_web.CoreWebView2 != null) _web.CoreWebView2.OpenDevToolsWindow();
                return true;
            }
            return base.ProcessCmdKey(ref msg, keyData);
        }

        private async Task InitAsync()
        {
            Program.Log("InitAsync 进入");
            if (_settings.Maximized) WindowState = FormWindowState.Maximized;

            // WebView2 的用户数据目录。首选标准位置；万一那个位置写不进去
            // （权限/安全软件/受限运行环境），退到程序目录，再退到临时目录。
            var candidates = new List<string>
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PanelV2Shell", "WebView2Data"),
                Path.Combine(AppContext.BaseDirectory, "userdata"),
                Path.Combine(Path.GetTempPath(), "PanelV2Shell", "WebView2Data"),
            };

            CoreWebView2Environment env = null;
            Exception last = null;
            string usedUdf = null;

            foreach (var udf in candidates)
            {
                for (int attempt = 1; attempt <= 2 && env == null; attempt++)
                {
                    try
                    {
                        Directory.CreateDirectory(udf);
                        Program.Log("CreateAsync " + udf + " 第" + attempt + "次");
                        env = await CoreWebView2Environment.CreateAsync(null, udf, null);
                        usedUdf = udf;
                        last = null;
                        Program.Log("环境就绪: " + env.BrowserVersionString + " udf=" + udf);
                    }
                    catch (Exception ex)
                    {
                        last = ex;
                        Program.Log("失败: " + ex.Message);
                        await Task.Delay(800);
                    }
                }
                if (env != null) break;
            }

            if (env == null)
            {
                Program.Log("WebView2 环境创建彻底失败: " + last);
                Fail("窗口内核（WebView2）没起来：\n\n" + (last == null ? "未知原因" : last.Message) +
                     "\n\n请确认已安装 Microsoft Edge WebView2 Runtime。\n" +
                     @"日志：%LOCALAPPDATA%\PanelV2Shell\shell.log");
                return;
            }

            try
            {
                await _web.EnsureCoreWebView2Async(env);
                Program.Log("WebView2 初始化完成");
            }
            catch (Exception ex)
            {
                Program.Log("EnsureCoreWebView2Async 失败: " + ex);
                Fail("窗口初始化失败：\n\n" + ex.Message);
                return;
            }

            var core = _web.CoreWebView2;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreDevToolsEnabled = true;
            core.Settings.IsZoomControlEnabled = true;
            core.Settings.AreDefaultContextMenusEnabled = true;
            core.NewWindowRequested += (s2, ev) => { ev.Handled = true; core.Navigate(ev.Uri); };
            core.WebMessageReceived += Core_WebMessageReceived;

            ShowStatus("正在连接面板服务…", false, false);
            await EnsurePanelAsync();
        }

        /// <summary>致命错误：提示一次，然后退出进程，别留一个占着单实例锁的空壳。</summary>
        private void Fail(string message)
        {
            if (_fatal) return;
            _fatal = true;
            try { MessageBox.Show(this, message, "作战面板 v2", MessageBoxButtons.OK, MessageBoxIcon.Error); }
            catch { }
            Close();
            Application.Exit();
        }

        private async void Core_WebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            string msg;
            try { msg = e.TryGetWebMessageAsString(); } catch { return; }
            if (msg == "retry" && !_busy)
            {
                ShowStatus("正在重新连接…", false, false);
                await EnsurePanelAsync();
            }
        }

        /// <summary>探活 → 没起来就拉 → 就绪后导航。全程在 UI 线程跑，但每一步都 await，不卡画面。</summary>
        private async Task EnsurePanelAsync()
        {
            if (_busy) return;
            _busy = true;
            try
            {
                Program.Log("探活 8810…");
                if (await ProbeAsync())
                {
                    Program.Log("8810 已就绪");
                    LoadPanel();
                    return;
                }

                Program.Log("8810 不通 -> 拉起");
                ShowStatus("面板服务没在跑，正在自动拉起…\n大约 10–30 秒，起来后会自动进面板。", false, false);
                LaunchService();

                for (int i = 0; i < 45; i++)
                {
                    await Task.Delay(1000);
                    if (await ProbeAsync())
                    {
                        LoadPanel();
                        return;
                    }
                }

                ShowStatus(
                    "面板服务 45 秒内没有起来。\n\n" +
                    "点下面「重试」再来一次；\n" +
                    "如果一直这样，日志在：\n" +
                    @"projects\panel-v2\data\panel_v2\logs\open_panel.log",
                    true, true);
            }
            finally
            {
                _busy = false;
            }
        }

        private void LoadPanel()
        {
            if (_web.CoreWebView2 == null) return;
            _panelLoaded = true;
            Program.Log("导航到 " + PanelBase);
            _web.CoreWebView2.Navigate(PanelBase + "/");
        }

        private static async Task<bool> ProbeAsync()
        {
            try
            {
                using (var resp = await Http.GetAsync(PanelBase + "/api/health"))
                    return resp.IsSuccessStatusCode;
            }
            catch { return false; }
        }

        /// <summary>调用项目里已有的保活脚本拉起 8810（它内部走 run.ps1，隐藏窗口）。</summary>
        private static void LaunchService()
        {
            var script = FindUp(@"tools\ensure_8810_once.ps1");
            if (script == null) return;
            var root = Directory.GetParent(Path.GetDirectoryName(script)).FullName;
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"",
                    WorkingDirectory = root,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                });
            }
            catch { }
        }

        private static string FindUp(string relative)
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            for (int i = 0; i < 5 && dir != null; i++, dir = dir.Parent)
            {
                var p = Path.Combine(dir.FullName, relative);
                if (File.Exists(p)) return p;
            }
            return null;
        }

        private void ShowStatus(string text, bool isError, bool showRetry)
        {
            if (_web.CoreWebView2 == null) return;
            _web.CoreWebView2.NavigateToString(BuildStatusPage(text, isError, showRetry));
        }

        private static string BuildStatusPage(string text, bool isError, bool showRetry)
        {
            var sb = new StringBuilder();
            sb.Append("<!doctype html><html><head><meta charset=\"utf-8\"><style>");
            sb.Append("html,body{height:100%;margin:0;background:#16181c;color:#c8ccd4;");
            sb.Append("font:14px/1.8 'Microsoft YaHei UI','Microsoft YaHei',sans-serif;");
            sb.Append("display:flex;align-items:center;justify-content:center;user-select:none}");
            sb.Append(".box{max-width:660px;padding:0 32px;text-align:center}");
            sb.Append(".t{font-size:16px;color:#e6e9ef;margin:0 0 10px}");
            sb.Append(".m{color:#8f96a3;white-space:pre-wrap}");
            sb.Append(".err .t{color:#ff8a7a}");
            sb.Append("button{margin-top:24px;padding:8px 24px;background:#2a2e36;color:#dfe3ea;");
            sb.Append("border:1px solid #3a404a;border-radius:6px;font-size:13px;cursor:pointer}");
            sb.Append("button:hover{background:#333843;border-color:#4a515d}");
            sb.Append(".spin{width:24px;height:24px;margin:0 auto 20px;border:2px solid #2f343d;");
            sb.Append("border-top-color:#6aa9ff;border-radius:50%;animation:r 1s linear infinite}");
            sb.Append("@keyframes r{to{transform:rotate(360deg)}}");
            sb.Append("</style></head><body><div class=\"box");
            if (isError) sb.Append(" err");
            sb.Append("\">");
            if (!isError) sb.Append("<div class=\"spin\"></div>");
            sb.Append("<div class=\"t\">作战面板 v2</div>");
            sb.Append("<div class=\"m\">").Append(EscapeHtml(text)).Append("</div>");
            if (showRetry) sb.Append("<button id=\"retry\">重 试</button>");
            sb.Append("</div><script>");
            sb.Append("var b=document.getElementById('retry');");
            sb.Append("if(b)b.addEventListener('click',function(){window.chrome.webview.postMessage('retry');});");
            sb.Append("</script></body></html>");
            return sb.ToString();
        }

        private static string EscapeHtml(string s)
        {
            return (s ?? "").Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");
        }
    }
}
