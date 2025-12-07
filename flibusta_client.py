"""
Flibusta Client - HTTP-based with OPDS fallback.

This module provides interface to search and download books from Flibusta.
Primary: HTTP/HTML parsing
Fallback: OPDS (when HTTP fails with errors)
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from urllib.parse import quote, urljoin
import httpx
import re
import logging

from config import FLIBUSTA_BASE_URL, REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

# OPDS endpoint
OPDS_BASE_URL = f"{FLIBUSTA_BASE_URL}/opds"

# XML namespaces for OPDS
NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'dc': 'http://purl.org/dc/terms/',
    'opds': 'http://opds-spec.org/2010/catalog'
}


@dataclass
class Author:
    """Represents an author."""
    id: str
    name: str
    uri: str = ""
    books_count: Optional[int] = None


@dataclass
class DownloadLink:
    """Represents a download link for a book."""
    url: str
    format: str
    mime_type: str = ""


@dataclass
class Book:
    """Represents a book from the catalog."""
    id: str
    title: str
    authors: List[Author] = field(default_factory=list)
    language: Optional[str] = None
    format: Optional[str] = None
    year: Optional[str] = None
    description: Optional[str] = None
    download_links: List[DownloadLink] = field(default_factory=list)
    cover_url: Optional[str] = None
    size: Optional[str] = None


@dataclass
class SearchResult:
    """Represents search results with pagination info."""
    items: List
    total_count: int = 0
    has_more: bool = False
    source: str = "http"


class FlibustaClient:
    """
    Flibusta client using HTTP/HTML parsing with OPDS fallback.
    
    Strategy:
    1. Try HTTP first (better parsing, alphabetical sorting)
    2. If HTTP fails (503, timeout, etc.), try OPDS as fallback
    """
    
    def __init__(self):
        self.client = httpx.Client(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT}
        )
    
    def check_connection(self) -> Tuple[bool, str]:
        """Check connection to Flibusta. Returns (success, message)."""
        logger.info("Checking connection to Flibusta...")
        
        # Try HTTP first
        try:
            response = self.client.get(FLIBUSTA_BASE_URL, timeout=15)
            if response.status_code == 200:
                return True, "✅ Подключение к Flibusta установлено (HTTP)"
        except Exception as e:
            logger.warning(f"HTTP check failed: {e}")
        
        # Try OPDS as fallback
        try:
            response = self.client.get(OPDS_BASE_URL, timeout=15)
            if response.status_code == 200:
                return True, "✅ Подключение к Flibusta установлено (OPDS)"
        except Exception as e:
            logger.warning(f"OPDS check failed: {e}")
        
        return False, "❌ Не удалось подключиться к Flibusta"
    
    def _clean_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        text = text.replace('&nbsp;', ' ').replace('&quot;', '"')
        return text.strip()
    
    # ================== HTTP Methods ==================
    
    def _http_request(self, url: str) -> Optional[str]:
        """Make HTTP request, returns None on error."""
        try:
            logger.info(f"HTTP request: {url}")
            response = self.client.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            return None
    
    def _http_search_books(self, query: str, page: int = 0) -> Optional[SearchResult]:
        """Search books via HTTP."""
        encoded_query = quote(query, safe='')
        url = f"{FLIBUSTA_BASE_URL}/booksearch?ask={encoded_query}&page={page}"
        
        html_content = self._http_request(url)
        if not html_content:
            return None
        
        try:
            books = []
            lines = html_content.split('\n')
            
            for line in lines:
                if '/s/' in line:  # Skip series
                    continue
                
                book_match = re.search(
                    r'<a[^>]*href=["\']?/b/(\d+)["\']?[^>]*>([^<]+)</a>',
                    line, re.IGNORECASE
                )
                
                if book_match:
                    book_id = book_match.group(1)
                    title = self._clean_html(book_match.group(2))
                    
                    if not title:
                        continue
                    
                    authors = []
                    author_match = re.search(
                        r'<a[^>]*href=["\']?/a/(\d+)["\']?[^>]*>([^<]+)</a>',
                        line, re.IGNORECASE
                    )
                    if author_match:
                        author_name = self._clean_html(author_match.group(2))
                        if author_name:
                            authors.append(Author(id=author_match.group(1), name=author_name))
                    
                    download_links = [
                        DownloadLink(url=f"{FLIBUSTA_BASE_URL}/b/{book_id}/fb2", format="fb2"),
                        DownloadLink(url=f"{FLIBUSTA_BASE_URL}/b/{book_id}/epub", format="epub"),
                        DownloadLink(url=f"{FLIBUSTA_BASE_URL}/b/{book_id}/mobi", format="mobi"),
                    ]
                    
                    books.append(Book(id=book_id, title=title, authors=authors, download_links=download_links))
            
            # Remove duplicates and sort
            seen_ids = set()
            unique_books = []
            for book in books:
                if book.id not in seen_ids:
                    seen_ids.add(book.id)
                    unique_books.append(book)
            unique_books.sort(key=lambda b: b.title.lower())
            
            has_more = f'page={page + 1}' in html_content
            
            return SearchResult(items=unique_books, has_more=has_more, source="http")
        except Exception as e:
            logger.error(f"HTTP parse error: {e}")
            return None
    
    def _http_search_authors(self, query: str) -> Optional[SearchResult]:
        """Search authors via HTTP."""
        encoded_query = quote(query, safe='')
        url = f"{FLIBUSTA_BASE_URL}/booksearch?ask={encoded_query}"
        
        html_content = self._http_request(url)
        if not html_content:
            return None
        
        try:
            authors = []
            
            author_section_match = re.search(
                r'Найденные писатели[^<]*</h3>\s*<ul>(.*?)</ul>',
                html_content, re.DOTALL | re.IGNORECASE
            )
            
            if author_section_match:
                author_section = author_section_match.group(1)
                author_pattern = re.compile(
                    r'<a[^>]*href=["\']?/a/(\d+)["\']?[^>]*>(.+?)</a>',
                    re.IGNORECASE | re.DOTALL
                )
                
                seen_ids = set()
                for match in author_pattern.finditer(author_section):
                    author_id = match.group(1)
                    name = self._clean_html(match.group(2))
                    
                    if author_id in seen_ids or not name:
                        continue
                    seen_ids.add(author_id)
                    authors.append(Author(id=author_id, name=name, uri=f"/a/{author_id}"))
            
            authors.sort(key=lambda a: a.name.lower())
            return SearchResult(items=authors, has_more=False, source="http")
        except Exception as e:
            logger.error(f"HTTP parse error: {e}")
            return None
    
    def _http_get_author_books(self, author_id: str) -> Optional[SearchResult]:
        """Get author books via HTTP (alphabetically, no series)."""
        url = f"{FLIBUSTA_BASE_URL}/a/{author_id}/alphabet"
        
        html_content = self._http_request(url)
        if not html_content:
            # Try without /alphabet
            url = f"{FLIBUSTA_BASE_URL}/a/{author_id}"
            html_content = self._http_request(url)
            if not html_content:
                return None
        
        try:
            books = []
            book_pattern = re.compile(
                r'<a[^>]*href=["\']?/b/(\d+)["\']?[^>]*>([^<]+)</a>',
                re.IGNORECASE
            )
            
            seen_ids = set()
            for match in book_pattern.finditer(html_content):
                book_id = match.group(1)
                title = self._clean_html(match.group(2))
                
                if book_id in seen_ids or not title:
                    continue
                if title.lower() in ['читать', 'скачать', 'fb2', 'epub', 'mobi', 'rtf', 'txt']:
                    continue
                
                seen_ids.add(book_id)
                
                download_links = [
                    DownloadLink(url=f"{FLIBUSTA_BASE_URL}/b/{book_id}/fb2", format="fb2"),
                    DownloadLink(url=f"{FLIBUSTA_BASE_URL}/b/{book_id}/epub", format="epub"),
                    DownloadLink(url=f"{FLIBUSTA_BASE_URL}/b/{book_id}/mobi", format="mobi"),
                ]
                
                books.append(Book(
                    id=book_id, title=title,
                    authors=[Author(id=author_id, name="")],
                    download_links=download_links
                ))
            
            books.sort(key=lambda b: b.title.lower())
            return SearchResult(items=books, has_more=False, source="http")
        except Exception as e:
            logger.error(f"HTTP parse error: {e}")
            return None
    
    # ================== OPDS Methods ==================
    
    def _opds_request(self, url: str) -> Optional[str]:
        """Make OPDS request, returns None on error."""
        try:
            logger.info(f"OPDS request: {url}")
            response = self.client.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as e:
            logger.error(f"OPDS error: {e}")
            return None
    
    def _opds_parse_author(self, author_elem) -> Author:
        """Parse author from OPDS XML."""
        name = author_elem.find('atom:name', NAMESPACES)
        uri = author_elem.find('atom:uri', NAMESPACES)
        
        author_id = ""
        if uri is not None and uri.text:
            match = re.search(r'/a/(\d+)', uri.text)
            if match:
                author_id = match.group(1)
        
        return Author(
            id=author_id,
            name=name.text if name is not None else "Unknown",
            uri=uri.text if uri is not None else ""
        )
    
    def _opds_parse_download_links(self, entry) -> List[DownloadLink]:
        """Parse download links from OPDS entry."""
        links = []
        format_map = {
            'application/fb2+zip': 'fb2', 'application/fb2': 'fb2',
            'application/epub+zip': 'epub', 'application/epub': 'epub',
            'application/pdf': 'pdf',
            'application/x-mobipocket-ebook': 'mobi',
        }
        
        for link in entry.findall('atom:link', NAMESPACES):
            rel = link.get('rel', '')
            if 'acquisition' in rel:
                href = link.get('href', '')
                mime_type = link.get('type', '')
                
                if 'text/html' in mime_type:
                    continue
                
                file_format = format_map.get(mime_type, mime_type.split('/')[-1])
                full_url = urljoin(FLIBUSTA_BASE_URL, href) if href.startswith('/') else href
                
                links.append(DownloadLink(url=full_url, format=file_format, mime_type=mime_type))
        
        return links
    
    def _opds_parse_book(self, entry) -> Book:
        """Parse book from OPDS entry."""
        book_id = ""
        for link in entry.findall('atom:link', NAMESPACES):
            href = link.get('href', '')
            match = re.search(r'/b/(\d+)', href)
            if match:
                book_id = match.group(1)
                break
        
        title = entry.find('atom:title', NAMESPACES)
        authors = [self._opds_parse_author(a) for a in entry.findall('atom:author', NAMESPACES)]
        language = entry.find('dc:language', NAMESPACES)
        content = entry.find('atom:content', NAMESPACES)
        
        return Book(
            id=book_id,
            title=title.text if title is not None else "Unknown",
            authors=authors,
            language=language.text if language is not None else None,
            description=content.text if content is not None else None,
            download_links=self._opds_parse_download_links(entry)
        )
    
    def _opds_search_books(self, query: str, page: int = 0) -> Optional[SearchResult]:
        """Search books via OPDS."""
        encoded_query = quote(query, safe='')
        url = f"{OPDS_BASE_URL}/opensearch?searchTerm={encoded_query}&searchType=books&pageNumber={page}"
        
        xml_content = self._opds_request(url)
        if not xml_content:
            return None
        
        try:
            root = ET.fromstring(xml_content)
            books = []
            
            for entry in root.findall('atom:entry', NAMESPACES):
                book = self._opds_parse_book(entry)
                if book.download_links:
                    books.append(book)
            
            # Check for next page
            has_more = any(link.get('rel') == 'next' for link in root.findall('atom:link', NAMESPACES))
            
            # Sort alphabetically
            books.sort(key=lambda b: b.title.lower())
            
            return SearchResult(items=books, has_more=has_more, source="opds")
        except ET.ParseError as e:
            logger.error(f"OPDS parse error: {e}")
            return None
    
    def _opds_search_authors(self, query: str, page: int = 0) -> Optional[SearchResult]:
        """Search authors via OPDS."""
        encoded_query = quote(query, safe='')
        url = f"{OPDS_BASE_URL}/opensearch?searchTerm={encoded_query}&searchType=authors&pageNumber={page}"
        
        xml_content = self._opds_request(url)
        if not xml_content:
            return None
        
        try:
            root = ET.fromstring(xml_content)
            authors = []
            
            for entry in root.findall('atom:entry', NAMESPACES):
                title = entry.find('atom:title', NAMESPACES)
                
                author_id = ""
                for link in entry.findall('atom:link', NAMESPACES):
                    href = link.get('href', '')
                    match = re.search(r'/author/(\d+)', href)
                    if match:
                        author_id = match.group(1)
                        break
                
                if title is not None and title.text:
                    authors.append(Author(id=author_id, name=title.text, uri=f"/a/{author_id}"))
            
            authors.sort(key=lambda a: a.name.lower())
            return SearchResult(items=authors, has_more=False, source="opds")
        except ET.ParseError as e:
            logger.error(f"OPDS parse error: {e}")
            return None
    
    def _opds_get_author_books(self, author_id: str) -> Optional[SearchResult]:
        """Get author books via OPDS."""
        # First get the author page to find alphabetical link
        url = f"{OPDS_BASE_URL}/author/{author_id}"
        
        xml_content = self._opds_request(url)
        if not xml_content:
            return None
        
        try:
            root = ET.fromstring(xml_content)
            
            # Look for alphabetical link
            target_url = None
            for entry in root.findall('atom:entry', NAMESPACES):
                title = entry.find('atom:title', NAMESPACES)
                title_text = title.text.lower() if title is not None and title.text else ""
                
                for link in entry.findall('atom:link', NAMESPACES):
                    href = link.get('href', '')
                    if "алфавит" in title_text or "alphabet" in href:
                        target_url = href
                        break
            
            if target_url:
                if not target_url.startswith('http'):
                    target_url = urljoin(FLIBUSTA_BASE_URL, target_url)
                xml_content = self._opds_request(target_url)
                if not xml_content:
                    return None
                root = ET.fromstring(xml_content)
            
            books = []
            for entry in root.findall('atom:entry', NAMESPACES):
                book = self._opds_parse_book(entry)
                if book.download_links:
                    books.append(book)
            
            books.sort(key=lambda b: b.title.lower())
            return SearchResult(items=books, has_more=False, source="opds")
        except ET.ParseError as e:
            logger.error(f"OPDS parse error: {e}")
            return None
    
    # ================== Public API (HTTP first, OPDS fallback) ==================
    
    def search_books(self, query: str, page: int = 0, status_callback=None) -> Optional[SearchResult]:
        """Search books. HTTP first, OPDS fallback."""
        # Try HTTP first
        result = self._http_search_books(query, page)
        if result and result.items:
            logger.info(f"HTTP returned {len(result.items)} books")
            return result
        
        # Fallback to OPDS
        logger.info("HTTP failed/empty, trying OPDS fallback for books")
        if status_callback:
            try:
                status_callback("⚠️ Проблемы с основным сервером, пробую запасной вариант...")
            except Exception as e:
                logger.error(f"Callback error: {e}")
                
        result = self._opds_search_books(query, page)
        if result and result.items:
            logger.info(f"OPDS returned {len(result.items)} books")
            return result
        
        return result
    
    def search_authors(self, query: str, page: int = 0, status_callback=None) -> Optional[SearchResult]:
        """Search authors. HTTP first, OPDS fallback."""
        # Try HTTP first
        result = self._http_search_authors(query)
        if result and result.items:
            logger.info(f"HTTP returned {len(result.items)} authors")
            return result
        
        # Fallback to OPDS
        logger.info("HTTP failed/empty, trying OPDS fallback for authors")
        if status_callback:
            try:
                status_callback("⚠️ Проблемы с основным сервером, пробую запасной вариант...")
            except Exception as e:
                logger.error(f"Callback error: {e}")
                
        result = self._opds_search_authors(query, page)
        if result and result.items:
            logger.info(f"OPDS returned {len(result.items)} authors")
            return result
        
        return result
    
    def get_author_books(self, author_id: str, page: int = 0, status_callback=None) -> Optional[SearchResult]:
        """Get author books. HTTP first, OPDS fallback."""
        # Try HTTP first
        result = self._http_get_author_books(author_id)
        if result and result.items:
            logger.info(f"HTTP returned {len(result.items)} author books")
            return result
        
        # Fallback to OPDS
        logger.info("HTTP failed/empty, trying OPDS fallback for author books")
        if status_callback:
            try:
                status_callback("⚠️ Проблемы с основным сервером, пробую запасной вариант...")
            except Exception as e:
                logger.error(f"Callback error: {e}")

        result = self._opds_get_author_books(author_id)
        if result and result.items:
            logger.info(f"OPDS returned {len(result.items)} author books")
            return result
        
        return result
    
    def download_book(self, url: str) -> Optional[Tuple[bytes, str]]:
        """Download a book file. Returns (content, filename) tuple."""
        try:
            logger.info(f"Downloading: {url}")
            response = self.client.get(url, follow_redirects=True)
            response.raise_for_status()
            
            content_disposition = response.headers.get('Content-Disposition', '')
            filename = None
            
            if 'filename=' in content_disposition:
                match = re.search(r'filename[*]?=["\']?([^"\';]+)', content_disposition)
                if match:
                    filename = match.group(1).strip()
                    if '%' in filename:
                        from urllib.parse import unquote
                        filename = unquote(filename)
            
            if not filename:
                filename = url.split('/')[-1]
                if '.' not in filename:
                    filename = "book"
            
            logger.info(f"Downloaded: {filename} ({len(response.content)} bytes)")
            return response.content, filename
            
        except httpx.HTTPError as e:
            logger.error(f"Download error: {e}")
            return None
    
    def close(self):
        """Close the HTTP client."""
        self.client.close()
