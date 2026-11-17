import re, unicodedata
SAMPLE={"values":["年度　 汇编\r\n上册","ＡＢＣ－００１","馆藏\t目录"]}
def width_only(value: str) -> str:
    return unicodedata.normalize("NFKC",value)
def space_only(value: str) -> str:
    value=value.replace("\r\n","\n").replace("\r","\n")
    return "\n".join(re.sub(r"[ \t]+"," ",line).strip() for line in value.split("\n"))
def normalize(value: str):
    return space_only(width_only(value))
PROFILES={"standard":normalize,"width":width_only,"space":space_only}
def normalize_with(profile: str, value: str) -> str:
    if profile not in PROFILES: raise KeyError(profile)
    return PROFILES[profile](value)
def evidence(value):
    new=normalize(value); return {"raw":value,"normalized":new,"changed":new!=value}
def run(payload):
    values=payload.get("values",SAMPLE["values"]); rows=[evidence(v) for v in values]; return {"status":"READY","input_count":len(rows),"valid_count":len(rows),"quarantine_count":0,"changed":sum(r["changed"] for r in rows),"values":[r["normalized"] for r in rows],"reason":"OK"}
