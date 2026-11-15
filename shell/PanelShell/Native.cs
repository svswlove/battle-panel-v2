using System;
using System.Runtime.InteropServices;

namespace PanelShell
{
    /// <summary>
    /// 单实例唤醒用到的三个小东西：一条自定义广播消息 + 抢前台。
    /// </summary>
    internal static class Native
    {
        public static readonly IntPtr HWND_BROADCAST = new IntPtr(0xFFFF);
        public static readonly uint WM_PANELSHELL_ACTIVATE = RegisterWindowMessage("PanelV2Shell.Activate");

        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint RegisterWindowMessage(string lpString);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool BringWindowToTop(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll")]
        private static extern bool IsIconic(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

        [DllImport("user32.dll")]
        private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();

        /// <summary>第二次双击时，通知已经在跑的那个壳把自己叫到前面。</summary>
        public static void BroadcastActivate()
        {
            if (WM_PANELSHELL_ACTIVATE == 0) return;
            PostMessage(HWND_BROADCAST, WM_PANELSHELL_ACTIVATE, IntPtr.Zero, IntPtr.Zero);
        }

        /// <summary>
        /// 抢前台。单靠 SetForegroundWindow 在「调用方不是前台进程」时会被系统忽略，
        /// 所以先把本线程挂到当前前台线程的输入队列上再抢（Windows 前台锁的老套路）。
        /// </summary>
        public static void ForceForeground(IntPtr h)
        {
            if (h == IntPtr.Zero) return;
            ShowWindow(h, IsIconic(h) ? 9 : 5); // 9=SW_RESTORE, 5=SW_SHOW

            IntPtr fg = GetForegroundWindow();
            uint fgPid;
            uint fgThread = (fg == IntPtr.Zero) ? 0 : GetWindowThreadProcessId(fg, out fgPid);
            uint me = GetCurrentThreadId();
            bool attached = false;

            if (fgThread != 0 && fgThread != me)
                attached = AttachThreadInput(me, fgThread, true);
            try
            {
                SetForegroundWindow(h);
                BringWindowToTop(h);
            }
            finally
            {
                if (attached) AttachThreadInput(me, fgThread, false);
            }
        }
    }
}
