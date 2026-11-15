# 生成作战面板 v2 图标（多尺寸 .ico，32bpp DIB 帧）
# 用法：& tools\make_icon.ps1
# 产物：app\web\panel-v2.ico（页面 favicon + 桌面快捷方式图标共用这一份）
# 配色取自 legacy/themes.css 默认深色主题：bg #1a2029 / card #232a37 / accent #4da3ff / up #ff5b5b / down #35c97f
# 用 DIB 帧而非 PNG 帧：.NET 的 Icon 类读 PNG 帧会报错，DIB 兼容面最大。

param(
    [string]$Out = (Join-Path $PSScriptRoot '..\app\web\panel-v2.ico')
)

Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;

public class PanelIcon
{
    static GraphicsPath RoundRect(float x, float y, float w, float h, float r)
    {
        GraphicsPath p = new GraphicsPath();
        float d = r * 2f;
        p.AddArc(x, y, d, d, 180, 90);
        p.AddArc(x + w - d, y, d, d, 270, 90);
        p.AddArc(x + w - d, y + h - d, d, d, 0, 90);
        p.AddArc(x, y + h - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }

    public static Bitmap Draw(int s)
    {
        float u = s / 256f;
        Bitmap bmp = new Bitmap(s, s, PixelFormat.Format32bppArgb);
        using (Graphics g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            g.Clear(Color.Transparent);

            GraphicsPath bg = RoundRect(6 * u, 6 * u, 244 * u, 244 * u, 54 * u);
            RectangleF rect = new RectangleF(6 * u, 6 * u, 244 * u, 244 * u);
            using (LinearGradientBrush b = new LinearGradientBrush(rect, Color.FromArgb(255, 0x2b, 0x33, 0x42), Color.FromArgb(255, 0x14, 0x1a, 0x22), 45f))
                g.FillPath(b, bg);
            using (Pen p = new Pen(Color.FromArgb(255, 0x3a, 0x46, 0x5a), Math.Max(1f, 3 * u)))
                g.DrawPath(p, bg);
            bg.Dispose();

            Color up = Color.FromArgb(255, 0xff, 0x5b, 0x5b);
            Color down = Color.FromArgb(255, 0x35, 0xc9, 0x7f);

            // 三根蜡烛：左低右高、红绿交替。>=32 画影线与基线，更小只留实体
            float cw = 34 * u;
            float[] cx = { 80 * u, 128 * u, 176 * u };
            float[] top = { 128 * u, 104 * u, 82 * u };
            float[] bot = { 186 * u, 166 * u, 144 * u };
            float[] wkT = { 106 * u, 82 * u, 60 * u };
            float[] wkB = { 206 * u, 186 * u, 164 * u };
            Color[] cols = { up, down, up };

            for (int i = 0; i < 3; i++)
            {
                if (s >= 32)
                {
                    using (Pen wp = new Pen(cols[i], Math.Max(1f, 6 * u)))
                    {
                        wp.StartCap = LineCap.Round;
                        wp.EndCap = LineCap.Round;
                        g.DrawLine(wp, cx[i], wkT[i], cx[i], wkB[i]);
                    }
                }
                using (GraphicsPath body = RoundRect(cx[i] - cw / 2, top[i], cw, bot[i] - top[i], 8 * u))
                using (SolidBrush sb = new SolidBrush(cols[i]))
                    g.FillPath(sb, body);
            }

            if (s >= 32)
            {
                using (Pen bp = new Pen(Color.FromArgb(255, 0x4a, 0x57, 0x6e), Math.Max(1f, 5 * u)))
                {
                    bp.StartCap = LineCap.Round;
                    bp.EndCap = LineCap.Round;
                    g.DrawLine(bp, 58 * u, 214 * u, 198 * u, 214 * u);
                }
            }
        }
        return bmp;
    }

    // 传统 ICO 帧：BITMAPINFOHEADER + 32bpp XOR(自下而上) + 1bpp AND
    public static byte[] Dib(int s)
    {
        using (Bitmap bmp = Draw(s))
        {
            BitmapData bd = bmp.LockBits(new Rectangle(0, 0, s, s), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
            int stride = bd.Stride;
            byte[] px = new byte[System.Math.Abs(stride) * s];
            Marshal.Copy(bd.Scan0, px, 0, px.Length);
            bmp.UnlockBits(bd);
            if (stride < 0) { stride = -stride; }

            int maskStride = ((s + 31) / 32) * 4;
            int maskSize = maskStride * s;
            int total = 40 + s * s * 4 + maskSize;
            byte[] buf = new byte[total];
            using (MemoryStream ms = new MemoryStream(buf))
            using (BinaryWriter bw = new BinaryWriter(ms))
            {
                bw.Write(40);            // biSize
                bw.Write(s);             // biWidth
                bw.Write(s * 2);         // biHeight（含 AND mask）
                bw.Write((short)1);      // biPlanes
                bw.Write((short)32);     // biBitCount
                bw.Write(0);             // biCompression = BI_RGB
                bw.Write(s * s * 4);     // biSizeImage
                bw.Write(0); bw.Write(0); bw.Write(0); bw.Write(0);
                for (int y = s - 1; y >= 0; y--) bw.Write(px, y * stride, s * 4);
                bw.Write(new byte[maskSize]);
            }
            return buf;
        }
    }
}
'@

$sizes = @(16, 24, 32, 48, 64, 128, 256)
$frames = @()
foreach ($s in $sizes) {
    $frames += , [PanelIcon]::Dib($s)
}

# 预览（人工核对用）
$tmp = Join-Path $PSScriptRoot '..\..\..\Hubagent\01\_tmp'
if (-not (Test-Path -LiteralPath $tmp)) { New-Item -ItemType Directory -Force -Path $tmp | Out-Null }

$bmp = [PanelIcon]::Draw(256)
$bmp.Save((Join-Path $tmp 'panel_icon_256.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

foreach ($ps in 16, 32, 48) {
    $b = [PanelIcon]::Draw($ps)
    $big = New-Object System.Drawing.Bitmap(($ps * 8), ($ps * 8))
    $g2 = [System.Drawing.Graphics]::FromImage($big)
    $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
    $g2.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
    $g2.DrawImage($b, 0, 0, $ps * 8, $ps * 8)
    $g2.Dispose(); $b.Dispose()
    $big.Save((Join-Path $tmp "panel_icon_$($ps)x8.png"), [System.Drawing.Imaging.ImageFormat]::Png)
    $big.Dispose()
}
Write-Host "previews -> $tmp\panel_icon_*.png"

# ---- 打包 ICO ----
$outPath = [System.IO.Path]::GetFullPath($Out)
$dir = Split-Path -Parent $outPath
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

$fs = [System.IO.File]::Create($outPath)
$bw = New-Object System.IO.BinaryWriter($fs)
$bw.Write([UInt16]0)
$bw.Write([UInt16]1)
$bw.Write([UInt16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i]
    $dim = if ($s -ge 256) { 0 } else { $s }
    $bw.Write([byte]$dim)
    $bw.Write([byte]$dim)
    $bw.Write([byte]0)
    $bw.Write([byte]0)
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]32)
    $bw.Write([UInt32]$frames[$i].Length)
    $bw.Write([UInt32]$offset)
    $offset += $frames[$i].Length
}
foreach ($f in $frames) { $bw.Write($f) }
$bw.Flush(); $bw.Close(); $fs.Close()

Write-Host ("ico -> {0} ({1} bytes, {2} frames)" -f $outPath, (Get-Item $outPath).Length, $sizes.Count)

# 同步一份到 ProgramData：桌面快捷方式的 IconLocation 指这里。
# 原因：lnk 指向 %USERPROFILE% 下的路径时，WScript.Shell 会把它存成环境变量形式，
# Shell 取图标会出问题（实测显示默认图标）；放 ProgramData 则写的是纯绝对路径。
$pubDir = 'C:\ProgramData\PanelV2'
try {
    if (-not (Test-Path -LiteralPath $pubDir)) { New-Item -ItemType Directory -Force -Path $pubDir | Out-Null }
    Copy-Item -LiteralPath $outPath -Destination (Join-Path $pubDir 'panel-v2.ico') -Force
    Write-Host ("copied -> {0}" -f (Join-Path $pubDir 'panel-v2.ico'))
} catch {
    Write-Warning "同步到 ProgramData 失败：$($_.Exception.Message)"
}
