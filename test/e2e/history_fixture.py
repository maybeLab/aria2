import argparse
import collections
import http.server
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request


def available_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


def rpc_request(port, secret, method, arguments=None, authenticated=True):
    parameters = (['token:' + secret] if authenticated else []) + (arguments or [])
    payload = json.dumps({'jsonrpc': '2.0', 'id': 'verification',
                          'method': method, 'params': parameters}).encode()
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/jsonrpc', data=payload,
        headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        result = json.load(response)
    if authenticated and 'error' in result:
        raise RuntimeError('{}: {}'.format(method, result['error']))
    return result.get('result') if authenticated else result


class Candidate:
    def __init__(self, executable, directory):
        self.executable = Path(executable).resolve()
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.port = available_port()
        self.secret = secrets.token_hex(32)
        self.process = None
        self.output = None
        self.session = self.directory / 'session.txt'
        self.database = self.directory / 'history.db'

    def start(self):
        download_directory = self.directory / 'downloads'
        arguments = [str(self.executable), '--no-conf=true', '--enable-rpc=true',
                     '--rpc-listen-all=false', f'--rpc-listen-port={self.port}',
                     f'--rpc-secret={self.secret}', '--enable-dht=false',
                     '--enable-dht6=false', '--bt-enable-lpd=false',
                     '--enable-peer-exchange=false', '--enable-color=false',
                     '--enable-sqlite3-persistence=true',
                     f'--sqlite3-db-path={self.database}',
                     '--sqlite3-history-limit=-1',
                     f'--dir={download_directory}',
                     f'--save-session={self.session}',
                     '--save-session-interval=1', '--max-tries=1',
                     '--max-concurrent-downloads=1', '--summary-interval=0',
                     '--console-log-level=warn', '--file-allocation=none',
                     '--disable-ipv6=true', '--check-certificate=true']
        self.output = (self.directory / 'candidate-output.log').open('ab')
        self.process = subprocess.Popen(
            arguments, cwd=self.directory, stdout=self.output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for attempt in range(100):
            if self.process.poll() is not None:
                raise RuntimeError('Candidate exited before RPC became ready')
            try:
                self.call('aria2.getVersion')
                return
            except (OSError, RuntimeError):
                time.sleep(0.1)
        raise RuntimeError('Candidate RPC startup timed out')

    def call(self, method, arguments=None):
        return rpc_request(self.port, self.secret, method, arguments)

    def stop(self, crash=False):
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                if crash:
                    self.process.kill()
                else:
                    self.call('aria2.shutdown')
                self.process.wait(timeout=20)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.output.close()
            self.process = None

    def wait_for(self, gid, status):
        for attempt in range(100):
            result = self.call('aria2.tellStatus', [gid])
            if result['status'] == status:
                return result
            time.sleep(0.1)
        raise AssertionError(f'Task did not reach expected status {status}')


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.request_counts[self.path] += 1
        payload = b'aria2 persistence verification ' * 4096
        self.send_response(404 if self.path == '/missing.bin' else 200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            if self.path == '/removed.bin':
                for offset in range(0, len(payload), 1024):
                    self.wfile.write(payload[offset:offset + 1024])
                    self.wfile.flush()
                    time.sleep(0.1)
            else:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def log_message(self, *arguments):
        return
