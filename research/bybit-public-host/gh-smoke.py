#!/usr/bin/env python3
"""GitHub runner evidence: exact public HTTP bodies and public WS frames, no auth."""
import asyncio
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT=Path("evidence")
ROOT.mkdir(exist_ok=True)
SETTINGS=json.loads(Path("research/bybit-public-host/github-probes.json").read_text())
manifest={"started":time.time(),"http":[],"websockets":[],"discoveries":[],"errors":[]}
SECRET_HEADERS={"cookie","set-cookie","authorization","x-api-key","x-bapi-api-key","x-bapi-sign"}
def safe_url(value):
    u=urlsplit(value)
    if u.scheme not in ("http","https") or not u.hostname or u.username or u.password:
        return None
    if any(k in u.path.lower() for k in ("/private","/auth","/wallet","/account","/orders","/positions","/execution")):
        return None
    if any(k in u.query.lower() for k in ("token","password","api_key","signature","session","jwt","ticket")):
        return None
    return urlunsplit((u.scheme,u.netloc,u.path or "/",u.query,""))
def event(kind,**details):
    with (ROOT/"events.jsonl").open("a",encoding="utf-8") as out:
        out.write(json.dumps({"timestamp":time.time(),"kind":kind,**details},ensure_ascii=False)+"\n")
def discovered(src,dst,kind):
    u=safe_url(dst)
    if not u:return
    manifest["discoveries"].append({"from":src,"to":u,"kind":kind})
    event("discovered",source=src,url=u,discovery_kind=kind)
class Links(HTMLParser):
    def __init__(self,base):
        super().__init__();self.base=base
    def handle_starttag(self,tag,attrs):
        for key,val in attrs:
            if key in ("src","href","poster") and val:
                discovered(self.base,urljoin(self.base,val),tag+":"+key)
async def capture_http():
    success=0
    for idx,source in enumerate(SETTINGS.get("http",[]),1):
        url=safe_url(source)
        if not url or urlsplit(url).hostname not in SETTINGS["http_hosts"]:
            continue
        folder=ROOT/"http";folder.mkdir(exist_ok=True)
        status=None;headers={};body=b"";effective=url
        try:
            request=Request(url,headers={"User-Agent":"PublicResearchValidation/1.1","Accept":"*/*"})
            with urlopen(request,timeout=18) as response:
                status=response.status
                effective=response.geturl()
                headers={k:v for k,v in response.headers.items() if k.lower() not in SECRET_HEADERS}
                body=response.read(5_000_001)
        except HTTPError as error:
            status=error.code
            headers={k:v for k,v in error.headers.items() if k.lower() not in SECRET_HEADERS}
            body=error.read(5_000_001)
        except (URLError, TimeoutError, OSError) as error:
            manifest["errors"].append({"url":url,"error":str(error)})
            event("http_error",url=url,error=str(error))
            continue
        if len(body)>5_000_000:
            manifest["errors"].append({"url":url,"error":"response exceeds 5 MB; excluded without truncating"})
            event("size_limit",url=url)
            continue
        path=folder/f"{idx:04}.response.bin";path.write_bytes(body)
        rec={"url":url,"effective_url":effective,"method":"GET","status":status,
             "headers":headers,"body_path":str(path),"bytes":len(body),
             "sha256":hashlib.sha256(body).hexdigest(),"timestamp":time.time()}
        manifest["http"].append(rec);event("http",url=url,status=status,path=str(path),bytes=len(body))
        if status==200:success+=1
        if "html" in headers.get("Content-Type",headers.get("content-type","")).lower():
            try:Links(url).feed(body.decode("utf-8","replace"))
            except Exception:pass
        print("HTTP",status,len(body),url,flush=True)
    return success
async def capture_ws():
    successes=0
    import websockets
    for spec in SETTINGS.get("ws",[]):
        url=spec["url"]
        u=urlsplit(url)
        if u.scheme!="wss" or u.hostname not in SETTINGS["ws_hosts"] or not u.path.startswith("/v5/public/"):
            continue
        request=spec.get("subscribe",{})
        if request.get("op")!="subscribe" or not isinstance(request.get("args"),list):continue
        if any(not isinstance(x,str) or len(x)>120 for x in request["args"]):continue
        filename=hashlib.sha256(url.encode()).hexdigest()[:16]+".jsonl"
        folder=ROOT/"ws";folder.mkdir(exist_ok=True)
        path=folder/filename
        def save(frame,direction):
            record={"timestamp":time.time(),"url":url,"direction":direction}
            if isinstance(frame,str):record.update(format="text",raw=frame)
            else:record.update(format="binary-base64",raw=base64.b64encode(frame).decode("ascii"))
            with path.open("a",encoding="utf-8") as f:f.write(json.dumps(record,ensure_ascii=False)+"\n")
            event("ws_frame",url=url,direction=direction,path=str(path))
        count=0
        try:
            async with websockets.connect(url,open_timeout=12,close_timeout=3,max_size=5_000_000) as ws:
                msg=json.dumps(request,ensure_ascii=False,separators=(",",":"))
                await ws.send(msg);save(msg,"sent")
                for _ in range(int(spec.get("max_frames",20))):
                    try:data=await asyncio.wait_for(ws.recv(),timeout=float(spec.get("seconds",6)))
                    except asyncio.TimeoutError:break
                    save(data,"received");count+=1
            if count:successes+=1
            print("WS",count,url,flush=True)
        except Exception as error:
            manifest["errors"].append({"url":url,"error":str(error)})
            event("ws_error",url=url,error=str(error))
        manifest["websockets"].append({"url":url,"frames_received":count,"raw_path":str(path)})
    return successes
async def main():
    count=0
    try:
        count+=await capture_http()
        count+=await capture_ws()
    finally:
        manifest["finished"]=time.time()
        manifest["successful_public_probes"]=count
        (ROOT/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print("PUBLIC VERIFIED:",count)
    if count<1:raise SystemExit("no public responses verified: see evidence/manifest.json")
if __name__=="__main__":
    asyncio.run(main())
