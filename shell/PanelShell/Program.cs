using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace PanelShell
{
    internal static class Program
    {
        private static Mutex _mutex;

        // 日志写在 %LOCALAPPDATA%\PanelV2Shell；“\WebView2” 目录被 WebView2 自己占用，不碰它。
        private static string LogPath
        {
            get
            {
                var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PanelV2Shell");
                Directory.CreateDirectory(dir);
                return Path.Combine(dir, "shell.log");
            }
        }

        internal static void Log(string msg)
        {
            var line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + msg + Environment.NewLine;
            try
            {
                File.AppendAllText(LogPath, line, new UTF8Encoding(false));
                return;
            }
            catch { }
            try
            {
                var alt = Path.Combine(Path.GetTempPath(), "PanelV2Shell");
                Directory.CreateDirectory(alt);
                File.AppendAllText(Path.Combine(alt, "shell.log"), line, new UTF8Encoding(false));
            }
            catch { }
        }

        [STAThread]
        static void Main()
        {
            Log("=== 启动 pid=" + Environment.ProcessId + " 目录=" + AppContext.BaseDirectory + " ===");

            AppDomain.CurrentDomain.UnhandledException += (s, e) =>
            {
                Log("未处理异常: " + e.ExceptionObject);
                MessageBox.Show("作战面板外壳崩溃：\n\n" + e.ExceptionObject,
                    "作战面板 v2", MessageBoxButtons.OK, MessageBoxIcon.Error);
            };
            Application.ThreadException += (s, e) => Log("界面线程异常: " + e.Exception);

            bool createdNew;
            _mutex = new Mutex(true, @"Local\PanelV2Shell", out createdNew);
            Log("Mutex createdNew=" + createdNew);
            if (!createdNew)
            {
                Log("已有实例在跑 -> 广播唤醒后退出");
                Native.BroadcastActivate();
                return;
            }

            try
            {
                Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new MainForm());
                Log("=== 正常退出 ===");
            }
            catch (Exception ex)
            {
                Log("Main 异常: " + ex);
                MessageBox.Show("作战面板外壳启动失败：\n\n" + ex.Message,
                    "作战面板 v2", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                try { _mutex.ReleaseMutex(); } catch { }
                _mutex.Dispose();
            }
        }
    }
}
