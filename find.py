with open("c:/Users/86183/Desktop/8960FYP/app.py", encoding="utf-8") as f:
    for i, l in enumerate(f):
         if "c1, c2" in l or "歷史明細" in l: print(f"{i}: {l.strip()}")
