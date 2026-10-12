"""Windows user-bound DPAPI provider settings. Never return secret values."""
import ctypes
import json
import os
from pathlib import Path

FIELDS=('ANTHROPIC_API_KEY','ZOYA_CLAUDE_MODEL','ELEVENLABS_API_KEY','ZOYA_VOICE_ID','ZOYA_VOICE_MODEL_ID','NARRATION_VOICE_ID','NARRATION_VOICE_MODEL_ID','PEXELS_API_KEY','ZOYA_ALLOW_PAID')
class Blob(ctypes.Structure):
    _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]

def crypt(raw,decrypt=False):
    if os.name!='nt':raise ValueError('Encrypted settings require Windows DPAPI')
    buffer=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    dll=ctypes.WinDLL('crypt32',use_last_error=True)
    func=dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    func.restype=ctypes.c_int
    func.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(Blob)]
    if not func(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):raise ValueError('Windows settings encryption failed')
    try:return ctypes.string_at(target.data,target.size)
    finally:
        free=ctypes.WinDLL('kernel32',use_last_error=True).LocalFree;free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
        free(ctypes.cast(target.data,ctypes.c_void_p))

def load(root):
    path=Path(root)/'data/provider-settings.dpapi'
    if not path.exists():return False
    if path.stat().st_size>20000:raise ValueError('Invalid encrypted settings size')
    values=json.loads(crypt(path.read_bytes(),True))
    if not isinstance(values,dict):raise ValueError('Invalid encrypted provider configuration')
    for key,value in values.items():
        if key not in FIELDS or not isinstance(value,str):raise ValueError('Invalid encrypted provider configuration')
    os.environ.update(values)
    return True

def save(root,values):
    if not isinstance(values,dict) or set(values)-set(FIELDS):raise ValueError('Unsupported provider settings')
    if any(not isinstance(v,str) or len(v)>2000 for v in values.values()):raise ValueError('Invalid provider setting')
    existing={k:os.environ[k] for k in FIELDS if os.environ.get(k)}
    for key,value in values.items():
        if value.strip():existing[key]=value.strip()
    existing['ZOYA_ALLOW_PAID']=values.get('ZOYA_ALLOW_PAID','0')
    if existing['ZOYA_ALLOW_PAID'] not in ('0','1'):raise ValueError('Invalid paid setting')
    encrypted=crypt(json.dumps(existing).encode())
    path=Path(root)/'data/provider-settings.dpapi';path.parent.mkdir(exist_ok=True)
    temporary=path.with_suffix('.partial');temporary.write_bytes(encrypted);os.replace(temporary,path)
    os.environ.update(existing)
    return {'saved':True,'encrypted':True,'values_returned':False,'upload_locked':True}
