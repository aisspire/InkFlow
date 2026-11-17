"""Read-only connectors. DNS is checked and the connection uses that exact address."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import time
from urllib.parse import urlsplit, urljoin
import xml.etree.ElementTree as ET

LIMIT = 512_000
TIMEOUT = 15


def checked_project_file(root, relative):
    """Project sources have their own read-only format contract, separate from articles."""
    from inkflow.web.documents import checked_location
    path = Path(relative)
    if path.anchor or len(relative) > 2000 or '\\' in relative or ':' in relative or any(p.startswith('.') for p in path.parts) or path.suffix.lower() not in {'.md', '.mdx', '.txt', '.rst'}:
        raise ValueError('项目来源仅支持工作区内的 Markdown、MDX、TXT、RST 相对文件路径；不读取隐藏文件或链接。')
    # Directory mode checks absolute paths and links without applying article extensions.
    checked = checked_location(Path(root) / path, directory=True)
    if not checked.is_relative_to(root):
        raise ValueError('项目来源不能读取工作区外路径。')
    return checked


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def url_parts(url):
    value = urlsplit(url)
    if value.scheme not in {'http', 'https'} or not value.hostname or value.username or value.password or value.fragment:
        raise ValueError('来源须为不含账号、密码和片段的 HTTP(S) 地址。')
    if any(ord(c) < 33 for c in url) or len(url) > 2000:
        raise ValueError('来源地址不合法。')
    return value


def origin(url):
    p = url_parts(url)
    return p.scheme, p.hostname, p.port or (443 if p.scheme == 'https' else 80)


def addresses(url, network):
    scheme, host, port = origin(url)
    found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not found:
        raise ValueError('来源域名没有可用地址。')
    for entry in found:
        address = ipaddress.ip_address(entry[4][0])
        if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
            raise ValueError('来源地址不在允许的网络范围内。')
        if network == 'public' and not address.is_global:
            raise ValueError('公开来源不能访问本机或内网；请显式创建内网来源。')
    return scheme, host, port, found[0][4][0]


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address, self.secure = address, secure

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        if self.secure:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


def read_http(source, cursor):
    url, start = source['url'], time.monotonic()
    headers = {'User-Agent': 'InkFlow-source/1', 'Accept-Encoding': 'identity'}
    if cursor.get('etag'):
        headers['If-None-Match'] = cursor['etag']
    if cursor.get('modified'):
        headers['If-Modified-Since'] = cursor['modified']
    secret = os.environ.get(source.get('credential_ref', ''), '')
    if source.get('credential_ref') and not secret:
        raise ValueError('来源凭据引用尚未配置。')
    if secret:
        if any(ord(c) < 32 or ord(c) > 126 for c in secret):
            raise ValueError('来源凭据格式无效，请检查引用的 Bearer 凭据。')
        if url_parts(url).scheme != 'https':
            raise ValueError('带凭据的来源必须使用 HTTPS。')
        headers['Authorization'] = 'Bearer ' + secret
    for _ in range(4):
        scheme, host, port, address = addresses(url, source['network'])
        remaining = TIMEOUT - (time.monotonic() - start)
        if remaining <= 0:
            raise ValueError('来源检查超时。')
        connection = PinnedConnection(host, port, address, scheme == 'https', remaining)
        try:
            parts = url_parts(url)
            connection.request('GET', parts.path or '/' if not parts.query else (parts.path or '/') + '?' + parts.query, headers=headers)
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                target = urljoin(url, response.getheader('Location', ''))
                if origin(target) != origin(source['url']):
                    raise ValueError('来源重定向到不同站点或端口，未扩大访问权限。')
                url = target
                continue
            if response.status == 304:
                return None
            if response.status != 200:
                raise ValueError(f'来源返回 HTTP {response.status}，稍后重试。')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('来源返回压缩内容，请提供可直接读取的来源。')
            try:
                content_length = int(response.getheader('Content-Length', '0'))
            except ValueError:
                raise ValueError('来源响应长度无效。') from None
            if content_length < 0:
                raise ValueError('来源响应长度无效。')
            if content_length > LIMIT:
                raise ValueError('来源超过 512 KB，未截断归档。')
            chunks, size = [], 0
            while True:
                remaining = TIMEOUT - (time.monotonic() - start)
                if remaining <= 0:
                    raise ValueError('来源检查超时。')
                # read1 returns available bytes; the total deadline also bounds slow streams.
                if connection.sock:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(16384, LIMIT + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk); size += len(chunk)
                if size > LIMIT:
                    raise ValueError('来源超过 512 KB，未截断归档。')
            content = b''.join(chunks).decode('utf-8-sig')
            if secret:
                content = content.replace(secret, '[已隐藏来源凭据]')
            metadata = {key: response.getheader(header, '')[:1000] for key, header in [('etag', 'ETag'), ('modified', 'Last-Modified')]}
            if secret:
                metadata = {k: v.replace(secret, '[已隐藏]') for k, v in metadata.items()}
            return {'url': url, 'content': content, **metadata}
        finally:
            connection.close()
    raise ValueError('来源重定向次数过多。')


def rss_content(content, url):
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', content, re.I):
        raise ValueError('RSS 不允许 DTD 或实体声明。')
    root = ET.fromstring(content)
    tag = lambda element: element.tag.rsplit('}', 1)[-1]
    if tag(root) not in {'rss', 'feed', 'RDF'}:
        raise ValueError('来源不是 RSS 或 Atom。')
    items = []
    for entry in root.iter():
        if tag(entry) not in {'item', 'entry'}:
            continue
        fields = {}
        for child in entry:
            key = tag(child)
            if key in {'title', 'link', 'guid', 'id', 'description', 'summary', 'content', 'updated', 'pubDate'}:
                fields[key] = child.attrib.get('href') or ''.join(child.itertext())
        link = urljoin(url, fields.get('link', ''))
        if urlsplit(link).scheme not in {'http', 'https'}:
            link = url
        fields['link'] = link
        items.append(fields)
        if len(items) > 200:
            raise ValueError('RSS 超过 200 条，请缩小来源范围。')
    # Feed timestamps and item order are not substantive changes.
    return json.dumps(sorted(items, key=lambda item: json.dumps(item, sort_keys=True)), ensure_ascii=False, sort_keys=True)


def read_source(source, cursor, documents, excluded):
    if source['kind'] == 'project':
        with documents.store.connection() as db:
            row = db.execute('SELECT root FROM workspaces WHERE id=?', (source['workspace_id'],)).fetchone()
        if not row:
            raise ValueError('来源工作区未登记。')
        root = Path(row['root'])
        entries, size = [], 0
        for relative in source['files']:
            path = checked_project_file(root, relative)
            if str(path.resolve()) in excluded:
                continue
            if not path.exists():
                entries.append({'path': relative, 'missing': True}); continue
            checked = path
            if not checked.is_file():
                raise ValueError('项目来源必须为普通文件。')
            if checked.stat().st_size + size > LIMIT:
                raise ValueError('项目来源合计超过 512 KB。')
            with checked.open('rb') as stream:
                raw = stream.read(LIMIT + 1 - size)
            size += len(raw)
            if size > LIMIT:
                raise ValueError('项目来源合计超过 512 KB。')
            content = raw.decode('utf-8-sig')
            documents.check_secret(content)
            entries.append({'path': relative, 'content': content, 'sha256': digest(content)})
        return {'url': 'workspace:' + source['workspace_id'], 'content': json.dumps(entries, ensure_ascii=False, sort_keys=True), 'etag': '', 'modified': ''}
    result = read_http(source, cursor)
    if result and source['kind'] == 'rss':
        result['raw_content'] = result['content']
        result['content'] = rss_content(result['content'], result['url'])
    if result:
        documents.check_secret(result['content'])
    return result
