//////////////////////////////////////////////////////////////////////////////
// Part of the Agnos RPC Framework
//    http://agnos.sourceforge.net
//
// Copyright 2011, International Business Machines Corp.
//                 Author: Tomer Filiba (tomerf@il.ibm.com)
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//////////////////////////////////////////////////////////////////////////////

using System;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Net.Sockets;
using System.Net.Security;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Diagnostics;
#if AGNOS_TRANSPORT_DEBUG
using System.Text;
#endif
using Agnos.Utils;


namespace Agnos.Transports
{
	public class TransportException : IOException
	{
		public TransportException (String message) : base(message)
		{
		}
	}

    /// <summary>
    /// the ITransport interface defines
    /// </summary>
	public interface ITransport : IDisposable
	{
        /// <summary>
        /// closes the transport and releases any system resources 
        /// associated with it
        /// </summary>
        void Close ();
        
        /// <summary>
        /// returns a Stream-view of this transport, that can be used 
        /// for reading
        /// </summary>
        /// <returns>Stream</returns>
		Stream GetInputStream ();
        
        /// <summary>
        /// returns a Stream-view of this transport, that can be used 
        /// for writing
        /// </summary>
        /// <returns>Stream</returns>
        Stream GetOutputStream();

        /// <summary>
        /// tests whether compression has been enabled on this transport
        /// </summary>
        /// <returns>whether compression is enabled on this transport</returns>
		bool IsCompressionEnabled ();
        
        /// <summary>
        /// attempts to enable compression on this transport. note that not
        /// all transports support compression, and the function will return
        /// a boolean indicating whether compression has been successfully
        /// enabled. initially, compression is disabled.
        /// 
        /// note that some implementations of libagnos may not support 
        /// compression, so before attempting to enable compression, be sure 
        /// that the remote side can handle compression.
        /// </summary>
        /// <returns>whether compression has been enabled on this transport</returns>
		bool EnableCompression ();
        
        /// <summary>
        /// disables the use of compression on this transport
        /// </summary>
		void DisableCompression ();

        //
		// read interface
        //

        /// <summary>
        /// begins a read transaction. only a single thread can hold an on-going 
        /// read transaction; if some thread has an on-going read transaction,
        /// any other thread calling this method will block.
        /// this method will block until a read transaction is received.
        /// </summary>
        /// <returns>transaction sequence number</returns>
		int BeginRead ();
        
        /// <summary>
        /// reads up to `len` bytes from the stream
        /// </summary>
        /// <param name="data">data (output) array</param>
        /// <param name="offset">the offset into the data array</param>
        /// <param name="len">the maximal number of bytes to read</param>
        /// <returns>the actual number of bytes read</returns>
		int Read (byte[] data, int offset, int len);
        
        /// <summary>
        /// finalizes the active read transaction. 
        /// </summary>
		void EndRead ();

        //
		// write interface
        //
        
        /// <summary>
        /// begins a write transaction. only a single thread can hold the 
        /// transaction at any point of time; if some thread has an on-going
        /// transaction, other threads calling this method will block.
        /// note that EndWrite() and CancelWrite() must be called to finalize the
        /// active transaction.
        /// </summary>
        /// <param name="seq"></param>
		void BeginWrite (int seq);
        
        /// <summary>
        /// writes the given data to the active write transaction. this method
        /// guarantees that all of the data will be written.
        /// </summary>
        /// <param name="data">data (input) array</param>
        /// <param name="offset">the offset into the data array</param>
        /// <param name="len">the number of bytes to write</param>
		void Write (byte[] data, int offset, int len);
        
        /// <summary>
        /// restarts the active write transaction (a rollback, discards all 
        /// the data written so far)
        /// </summary>
		void RestartWrite ();
        
        /// <summary>
        /// finalizes the active transaction (commits all the data written so far)
        /// </summary>
		void EndWrite ();
        
        /// <summary>
        /// cancels the active write transaction (nothing will be written to the 
        /// stream)
        /// </summary>
		void CancelWrite ();
	}

	/// <summary>
	/// an implementation of an input/output stream over a transport
	/// </summary>
	internal sealed class TransportStream : Stream
	{
		readonly private ITransport transport;
		readonly private bool output;
		
		public TransportStream (ITransport transport, bool output)
		{
			this.transport = transport;
			this.output = output;
		}

		public override void Close ()
		{
			if (output) {
				transport.EndWrite();
			}
			else {
				transport.EndRead();
			}
		}

		public override void Write (byte[] buffer, int offset, int count)
		{
			if (!output) {
				throw new InvalidOperationException("this stream is opened for reading only");
			}
			transport.Write (buffer, offset, count);
		}
		public override int Read (byte[] buffer, int offset, int count)
		{
			if (output) {
				throw new InvalidOperationException("this stream is opened for writing only");
			}
			return transport.Read (buffer, offset, count);
		}

		public override bool CanRead {
			get { return !output; }
		}
		public override bool CanSeek {
			get { return false; }
		}
		public override bool CanWrite {
			get { return output; }
		}
		public override long Length {
			get {
				throw new IOException ("not implemented");
			}
		}
		public override long Position {
			get {
				throw new IOException ("not implemented");
			}
			set {
				throw new IOException ("not implemented");
			}
		}
		public override void SetLength (long value)
		{
			throw new IOException ("not implemented");
		}
		public override long Seek (long offset, SeekOrigin origin)
		{
			throw new IOException ("not implemented");
		}
		public override void Flush ()
		{
		}
	}

    /// <summary>
    /// implements the common logic that is shared between (virtually) 
    /// all concrete transports
    /// </summary>
	public abstract class BaseTransport : ITransport
	{
		protected const int INITIAL_BUFFER_SIZE = 128 * 1024;

		protected Stream inStream;
		protected Stream outStream;

		protected readonly MemoryStream wbuffer = new MemoryStream (INITIAL_BUFFER_SIZE);
		protected readonly MemoryStream compressionBuffer = new MemoryStream (INITIAL_BUFFER_SIZE);
		private readonly TransportStream asInputStream;
		private readonly TransportStream asOutputStream;
		protected readonly ReentrantLock rlock = new ReentrantLock ();
		protected readonly ReentrantLock wlock = new ReentrantLock ();

		protected BoundInputStream readStream;
		protected int wseq = 0;
		protected int compressionThreshold = -1;

		public BaseTransport (Stream inOutStream) : this(inOutStream, inOutStream)
		{
		}

		public BaseTransport (Stream inStream, Stream outStream)
		{
			this.inStream = inStream;
			this.outStream = outStream;
			asInputStream = new TransportStream (this, false);
			asOutputStream = new TransportStream (this, true);
		}

        ~BaseTransport()
        {
            Close();
        }

		public void Dispose ()
		{
			Close();
            GC.SuppressFinalize(this);
		}

		public virtual void Close ()
		{
			if (inStream != null) {
				inStream.Close ();
				inStream = null;
			}
			if (outStream != null) {
				outStream.Close ();
				outStream = null;
			}
		}

		public virtual Stream GetInputStream ()
		{
			return asInputStream;
		}
		public virtual Stream GetOutputStream ()
		{
			return asOutputStream;
		}
		public virtual bool IsCompressionEnabled ()
		{
			return compressionThreshold > 0;
		}
		public virtual bool EnableCompression ()
		{
			compressionThreshold = getCompressionThreshold ();
			return compressionThreshold > 0;
		}
		public virtual void DisableCompression ()
		{
			compressionThreshold = -1;
		}

        /// <summary>
        /// returns the compression threshold (packets larger than this threshold 
        /// will be compressed).
        /// this method is expected to be overriden by implementing classes
        /// </summary>
        /// <returns>the compression threshold; a negative number means 
        /// compression is not supported</returns>
		protected virtual int getCompressionThreshold ()
		{
			return -1;
		}

		protected static int readSInt32(Stream stream) 
		{
			byte[] buf = new byte[4];
			int len = stream.Read(buf, 0, buf.Length);
			if (len < buf.Length) {
				throw new EndOfStreamException("expected " + buf.Length + " bytes, got " + len);
			}
			return ((int)(buf[0] & 0xff) << 24) | 
					((int)(buf[1] & 0xff) << 16) | 
					((int)(buf[2] & 0xff) << 8) | 
					((int)(buf[3] & 0xff));
		}

		protected static void writeSInt32(Stream stream, int val) 
		{
			byte[] buf = {(byte)((val >> 24) & 0xff),
				(byte)((val >> 16) & 0xff),
				(byte)((val >> 8) & 0xff),
				(byte)((val) & 0xff)};
			stream.Write(buf, 0, buf.Length);
		}

#if AGNOS_TRANSPORT_DEBUG
		internal static String repr(byte[] arr) {
			return repr(arr, 0, arr.Length);
		}

		internal static String repr(byte[] arr, int offset, int len) {
			StringBuilder sb = new StringBuilder(5000);
			sb.Append("\"");
			for (int i = offset; i < offset + len; i++) {
				byte b = arr[i];
				if (b < 32 || b > 127 || b == '"') {
					sb.AppendFormat("\\x{0:X2}", b);
				}
				else {
					sb.Append((char) b);
				}
			}
			sb.Append("\"");
			return sb.ToString();
		}
#endif

		//
		// read interface
		//
		public virtual int BeginRead ()
		{
			if (rlock.IsHeldByCurrentThread ()) {
				throw new IOException ("BeginRead is not reentrant");
			}

#if AGNOS_TRANSPORT_DEBUG
			System.Console.WriteLine("TransportBegin.BeginRead");
#endif				
			rlock.Acquire ();

			try {
				int seq = readSInt32(inStream);
				int packetLength = readSInt32(inStream);
				int uncompressedLength = readSInt32(inStream);

#if AGNOS_TRANSPORT_DEBUG
				System.Console.WriteLine(">> seq={0}, len={1}, uncomp={2}", seq, packetLength, uncompressedLength);
#endif				
				if (readStream != null) {
					throw new InvalidOperationException ("readStream must be null at this point");
				}
				
				readStream = new BoundInputStream (inStream, packetLength, true, false);
				if (uncompressedLength > 0) {
					readStream = new BoundInputStream (new DeflateStream (readStream, CompressionMode.Decompress, false), packetLength, false, true);
				}
				return seq;
			} catch (Exception) {
				readStream = null;
				rlock.Release ();
				throw;
			}
		}

		protected void AssertBeganRead ()
		{
			if (!rlock.IsHeldByCurrentThread ()) {
				throw new IOException ("thread must first call BeginRead");
			}
		}

		public virtual int Read (byte[] data, int offset, int len)
		{
			AssertBeganRead ();
#if AGNOS_TRANSPORT_DEBUG
		    System.Console.WriteLine("Transport.Read len={0}", len);
#endif
			if (len > readStream.Available) {
				throw new EndOfStreamException ("request to read more than available");
			}
#if AGNOS_TRANSPORT_DEBUG
			int cnt = readStream.Read (data, offset, len);
		    System.Console.WriteLine(">> " + repr(data, offset, len));
		    return cnt;
#else
			return readStream.Read (data, offset, len);
#endif
		}

		public virtual void EndRead ()
		{
			AssertBeganRead ();
#if AGNOS_TRANSPORT_DEBUG
		    System.Console.WriteLine("Transport.EndRead");
#endif
			readStream.Close ();
			readStream = null;
			rlock.Release ();
#if AGNOS_TRANSPORT_DEBUG
		    System.Console.WriteLine(">> okay");
#endif
		}

		//
		// write interface
		//
		public virtual void BeginWrite (int seq)
		{
			if (wlock.IsHeldByCurrentThread ()) {
				throw new IOException ("BeginWrite is not reentrant");
			}
#if AGNOS_TRANSPORT_DEBUG
			System.Console.WriteLine("Transport.BeginWrite");
#endif
			wlock.Acquire ();
			wseq = seq;
			wbuffer.Position = 0;
			wbuffer.SetLength (0);
#if AGNOS_TRANSPORT_DEBUG
			System.Console.WriteLine(">> okay");
#endif
		}

		protected virtual void AssertBeganWrite ()
		{
			if (!wlock.IsHeldByCurrentThread ()) {
				throw new IOException ("thread must first call BeginWrite");
			}
		}

		public virtual void Write (byte[] data, int offset, int len)
		{
			AssertBeganWrite ();
#if AGNOS_TRANSPORT_DEBUG
			System.Console.WriteLine("Transport.Write len={0}", len);
//		    System.Console.WriteLine(">> " + repr(data, offset, len));
#endif
			wbuffer.Write (data, offset, len);
		}

		public virtual void RestartWrite ()
		{
			AssertBeganWrite ();
#if AGNOS_TRANSPORT_DEBUG
			System.Console.WriteLine("Transport.RestartWrite");
#endif
			wbuffer.Position = 0;
			wbuffer.SetLength (0);
		}

		public virtual void EndWrite ()
		{
#if AGNOS_TRANSPORT_DEBUG
			System.Console.WriteLine("Transport.EndWrite");
#endif
			AssertBeganWrite ();
			if (wbuffer.Length > 0) {
				writeSInt32(outStream, wseq);
				
				if (compressionThreshold > 0 && wbuffer.Length >= compressionThreshold) {
					compressionBuffer.Position = 0;
					compressionBuffer.SetLength (0);
					using (DeflateStream dfl = new DeflateStream (compressionBuffer, CompressionMode.Compress, true)) {
						wbuffer.WriteTo (dfl);
					}
#if AGNOS_TRANSPORT_DEBUG
				    //System.Console.WriteLine("Transport.EndWrite seq={0}, len={1}, uncomp={2}", wseq, compressionBuffer.Length, wbuffer.Length);
				    System.Console.WriteLine(">> " + repr(wbuffer.ToArray()));
#endif
					writeSInt32 (outStream, (int)compressionBuffer.Length); // packet length
					writeSInt32 (outStream, (int)wbuffer.Length); // uncompressed length
					compressionBuffer.WriteTo (outStream);
				} 
				else {
#if AGNOS_TRANSPORT_DEBUG
				    System.Console.WriteLine(">> seq={0}, len={1}, uncomp=0", wseq, wbuffer.Length);
				    System.Console.WriteLine(">> " + repr(wbuffer.ToArray()));
#endif
					writeSInt32 (outStream, (int)wbuffer.Length); // packet length
					writeSInt32 (outStream, 0); // 0 means no compression
					wbuffer.WriteTo (outStream);
				}
				
				outStream.Flush ();
			}
			
			wbuffer.Position = 0;
			wbuffer.SetLength (0);
			wlock.Release ();
		}

		public virtual void CancelWrite ()
		{
			AssertBeganWrite ();
#if AGNOS_TRANSPORT_DEBUG
			System.Console.WriteLine("Transport.CancelWrite");
#endif
			wbuffer.Position = 0;
			wbuffer.SetLength (0);
			wlock.Release ();
		}
	}

	/// <summary>
	/// implements a transport that wraps an underlying transport
	/// </summary>
	public abstract class WrappedTransport : ITransport
	{
		protected ITransport transport;

		public WrappedTransport (ITransport transport)
		{
			this.transport = transport;
		}
		public Stream GetInputStream ()
		{
			return transport.GetInputStream ();
		}
		public Stream GetOutputStream ()
		{
			return transport.GetOutputStream ();
		}
		public bool IsCompressionEnabled ()
		{
			return this.IsCompressionEnabled ();
		}
		public bool EnableCompression ()
		{
			return transport.EnableCompression ();
		}
		public void DisableCompression ()
		{
			transport.DisableCompression ();
		}
		public void Dispose ()
		{
			transport.Dispose();
		}
		public void Close ()
		{
			transport.Close ();
		}
		public int BeginRead ()
		{
			return transport.BeginRead ();
		}
		public int Read (byte[] data, int offset, int len)
		{
			return transport.Read (data, offset, len);
		}
		public void EndRead ()
		{
			transport.EndRead ();
		}
		public void BeginWrite (int seq)
		{
			transport.BeginWrite (seq);
		}
		public void Write (byte[] data, int offset, int len)
		{
			transport.Write (data, offset, len);
		}
		public void RestartWrite ()
		{
			transport.RestartWrite ();
		}
		public void EndWrite ()
		{
			transport.EndWrite ();
		}
		public void CancelWrite ()
		{
			transport.EndWrite ();
		}
	}

	/// <summary>
	/// an implementation of a transport over sockets.
	/// example:
	///     SocketTransport t = new SocketTransport("localhost", 12345)
	/// </summary>
	public class SocketTransport : BaseTransport
	{
		protected readonly Socket sock;
		public const int DEFAULT_BUFSIZE = 16 * 1024;
		public const int DEFAULT_COMPRESSION_THRESHOLD = 4 * 1024;

		public SocketTransport (Socket sock) : 
			base(new BufferedStream (new NetworkStream (sock, true), DEFAULT_BUFSIZE))
		{
			this.sock = sock;
		}

		public SocketTransport (String host, int port) : this(_connect (host, port))
		{
		}

		public SocketTransport (IPAddress addr, int port) : this(_connect (addr, port))
		{
		}

		//
		
		static internal Socket _connect (String host, int port)
		{
			Socket sock = new Socket (AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
			sock.Connect (host, port);
			return sock;
		}

		static internal Socket _connect (IPAddress addr, int port)
		{
			Socket sock = new Socket (addr.AddressFamily, SocketType.Stream, ProtocolType.Tcp);
			sock.Connect (addr, port);
			return sock;
		}
	}

	/// <summary>
	/// an implementation of a transport over SSL sockets.
	/// example:
	///     SslSocketTransport t = new SslSocketTransport("localhost", 12345)
	///
	/// in order to configure an SSL connection, create an SslStream and pass 
	/// it to the constructor
	/// </summary>
	public class SslSocketTransport : BaseTransport
	{
		public const int DEFAULT_COMPRESSION_THRESHOLD = 4 * 1024;
		protected readonly Socket sock;

		public SslSocketTransport (SslStream stream) : this(stream, SocketTransport.DEFAULT_BUFSIZE)
		{
		}

		public SslSocketTransport (SslStream stream, int bufsize) : base(new BufferedStream (stream, bufsize))
		{
			this.sock = null;
		}

		public SslSocketTransport (String host, int port) : this(SocketTransport._connect (host, port))
		{
		}

		public SslSocketTransport (IPAddress addr, int port) : this(SocketTransport._connect (addr, port))
		{
		}

		public SslSocketTransport (Socket sock) : 
			base(new BufferedStream (new SslStream (new NetworkStream (sock, true), false), SocketTransport.DEFAULT_BUFSIZE))
		{
			this.sock = sock;
		}
	}

	/// <summary>
	/// a transport to a newly created process.
	/// use one of the Connect() variants to spawn the server process and 
	/// establish a connection to it. the server process is expected to operate
	/// in library mode, and is thus expected to die when the connection is 
	/// closed.
	/// </summary>
	public class ProcTransport : WrappedTransport
	{
		public readonly Process proc;

		public ProcTransport (Process proc, ITransport transport) : base(transport)
		{
			this.proc = proc;
		}

		public static ProcTransport Connect (string filename)
		{
			return Connect (filename, "-m lib");
		}

		public static ProcTransport Connect (string filename, string args)
		{
			Process proc = new Process ();
			proc.StartInfo.UseShellExecute = false;
			proc.StartInfo.FileName = filename;
			proc.StartInfo.Arguments = args;
			proc.StartInfo.CreateNoWindow = true;
			proc.StartInfo.RedirectStandardInput = true;
			proc.StartInfo.RedirectStandardOutput = true;
			proc.StartInfo.RedirectStandardError = true;
			return Connect (proc);
		}

		public static ProcTransport Connect (Process proc)
		{
			proc.Start ();
			if (proc.StandardOutput.ReadLine () != "AGNOS") {
				throw new TransportException ("process " + proc + " did not start correctly");
			}
			string hostname = proc.StandardOutput.ReadLine ();
			int port = Int16.Parse (proc.StandardOutput.ReadLine ());
			ITransport transport = new SocketTransport (hostname, port);
			return new ProcTransport (proc, transport);
		}
	}
}
