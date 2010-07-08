from cStringIO import StringIO
from httplib import HTTPConnection, HTTPSConnection
from urlparse import urlparse
from .transports import Transport


class HttpRequest(object):
    def __init__(self, conn, path, method="POST"):
        self.conn = conn
        self.path = path
        self.method = method
        self.headers = {}
    def __getitem__(self, key):
        return self.headers[key]
    def __setitem__(self, key, value):
        self.headers[key] = value
    def send(self, body):
        self.conn.request(self.method, self.path, body, self.headers)
        return conn.getresponse()


class HttpClientTransport(Transport):
    def __init__(self, url):
        Transport.__init__(self, None, None)
        self.url = url
        parsed = urlparse(url)
        self.urlprot = parsed.scheme.lower()
        self.urlhost = parsed.netloc
        self.urlpath = parsed.path
        self.conn = None
    
    def close(self):
        self.infile = None
        self.outfile = None
    
    def _build_request(self):
        if self.conn is None:
            if self.urlprot == "http":
                self.conn = HTTPConnection(self.urlhost)
            elif self.urlprot == "http":
                #TODO: key_file, cert_file, strict
                self.conn = HTTPSConnection(self.urlhost)
            else:
                raise ValueError("invalid url protocol: %r" % (self.urlprot,))
            self.conn.connect()
        req = HttpRequest(self.conn, self.urlpath)
        req["Content-type"] = "application/octet-stream"
        return req

    def begin_read(timeout = None):
        if not self.infile:
            raise IOError("begin_read must be called only after end_write")
        return Transport.begin_read(self, timeout)
    
    def end_read(self):
        if not self._rlock.is_held_by_current_thread():
            raise IOError("thread must first call begin_read")
        self.infile = None
        self._rlock.release()
    
    def end_write(self):
        if not self._wlock.is_held_by_current_thread():
            raise IOError("thread must first call begin_write")
        data = "".join(self._write_buffer)
        del self._write_buffer[:]
        if data:
            outstream = StringIO()
            packers.Int32.pack(len(data), outstream)
            packers.Int32.pack(self._write_seq, outstream)
            prefix = outstream.getvalue()
            req = self._build_request()
            self.infile = req.send(prefix + data)
        
        self._wlock.release()








