"""Streaming uncompressed MP4 ZIPs: bounded chunks, no second archive on disk."""
import zipfile

class Sink:
    def __init__(self):self.position=0;self.chunks=[]
    def write(self,value):
        self.position+=len(value);self.chunks.append(value);return len(value)
    def tell(self):return self.position
    def flush(self):pass
    def seekable(self):return False
    def drain(self):
        chunks,self.chunks=self.chunks,[]
        return chunks

def zip_exports(files):
    sink=Sink()
    with zipfile.ZipFile(sink,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for name,path in files:
            with archive.open(name,'w',force_zip64=True) as item,path.open('rb') as source:
                while chunk:=source.read(256*1024):
                    item.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()
