using System;
using System.Drawing;
using System.IO;
using System.Text.Json;
using System.Windows.Forms;

namespace PanelShell
{
    /// <summary>
    /// 窗口位置/大小/最大化状态。存 %LOCALAPPDATA%\PanelV2Shell\window.json。
    /// 存不住就退回默认值，绝不让它影响启动。
    /// </summary>
    internal sealed class AppSettings
    {
        public int X { get; set; } = int.MinValue;
        public int Y { get; set; } = int.MinValue;
        public int W { get; set; } = 1500;
        public int H { get; set; } = 1000;
        public bool Maximized { get; set; }

        private static string FilePath
        {
            get
            {
                var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PanelV2Shell");
                Directory.CreateDirectory(dir);
                return Path.Combine(dir, "window.json");
            }
        }

        public static AppSettings Load()
        {
            try
            {
                if (File.Exists(FilePath))
                {
                    var s = JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(FilePath));
                    if (s != null && s.W >= 800 && s.H >= 600) return s;
                }
            }
            catch { }
            return new AppSettings();
        }

        public Rectangle Bounds()
        {
            var screen = Screen.PrimaryScreen.WorkingArea;
            if (X == int.MinValue || Y == int.MinValue)
            {
                int w = Math.Min(W, screen.Width);
                int h = Math.Min(H, screen.Height);
                return new Rectangle(screen.X + (screen.Width - w) / 2, screen.Y + (screen.Height - h) / 2, w, h);
            }
            // 位置可能在已拔掉的副屏上，落在所有屏幕之外就拉回主屏
            var r = new Rectangle(X, Y, W, H);
            bool visible = false;
            foreach (var sc in Screen.AllScreens)
            {
                if (sc.WorkingArea.IntersectsWith(r)) { visible = true; break; }
            }
            if (!visible)
                r = new Rectangle(screen.X + 60, screen.Y + 60, Math.Min(W, screen.Width - 120), Math.Min(H, screen.Height - 120));
            return r;
        }

        public void SaveFrom(Form f)
        {
            try
            {
                Maximized = f.WindowState == FormWindowState.Maximized;
                if (f.WindowState == FormWindowState.Normal)
                {
                    X = f.Bounds.X; Y = f.Bounds.Y; W = f.Bounds.Width; H = f.Bounds.Height;
                }
                File.WriteAllText(FilePath, JsonSerializer.Serialize(this));
            }
            catch { }
        }
    }
}
