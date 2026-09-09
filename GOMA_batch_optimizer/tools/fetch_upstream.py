#!/usr/bin/env python3
"""Fetch a pinned source snapshot, verifying Git blob hashes. Never overwrites files."""
import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen
COMMIT='b4015e465d78a8dbeb25ec9220cfe7c34883a865'
BLOBS={'full_model.py':'8979f7b0c2812ea3b5012d27b748e900e4938f65',
       'normalized_energy_model.py':'67195293bb88906047ffbcb4c629a812fdb2c350',
       'LICENSE':'f92070835f01cdcab946a306b881cff32ae754c2'}
p=argparse.ArgumentParser();p.add_argument('directory',type=Path);a=p.parse_args()
a.directory.mkdir(parents=True,exist_ok=True)
for name,expected in BLOBS.items():
    target=a.directory/name
    if target.exists():
        data=target.read_bytes().replace(b'\r\n',b'\n')
    else:
        url=f'https://raw.githubusercontent.com/ywlywl6/GOMA/{COMMIT}/{name}'
        with urlopen(url,timeout=30) as response: data=response.read()
    sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    if sha!=expected: raise RuntimeError(f'Source hash mismatch: {name}')
    if not target.exists(): target.write_bytes(data)
    print(name,sha)
