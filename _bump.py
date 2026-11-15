import io, os, glob
n=0
for p in glob.glob("app/plugins/kline/**/*.js", recursive=True) + glob.glob("app/plugins/kline/**/*.html", recursive=True):
    s = io.open(p, encoding="utf-8").read()
    if "0.9.55" in s:
        io.open(p, "w", encoding="utf-8", newline="").write(s.replace("0.9.55", "0.9.56"))
        print("patched", p)
        n+=1
print("total", n)
