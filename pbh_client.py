import os
import re
import requests
from playwright.sync_api import sync_playwright

class PBHClient:
    """Client to scrape and download ITBI transaction datasets and IPTU data from PBH Open Data Portal."""
    
    DATASET_URL = "https://dados.pbh.gov.br/dataset/itbi-relatorios"
    
    def __init__(self, download_dir="data/raw/itbi", iptu_dir="data/raw/iptu"):
        self.download_dir = download_dir
        self.iptu_dir = iptu_dir
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(self.iptu_dir, exist_ok=True)
        
    def fetch_2026_csv_urls(self):
        """Launches a headless browser to fetch all CSV download links for the year 2026."""
        print(f"Opening PBH dataset page: {self.DATASET_URL}")
        csv_downloads = []
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True
            )
            page = browser.new_page()
            
            try:
                page.goto(self.DATASET_URL, wait_until="networkidle", timeout=30000)
                
                # Query all links
                links = page.query_selector_all("a")
                for link in links:
                    href = link.get_attribute("href")
                    if href and "/download/" in href and "_2026" in href:
                        # Deduplicate
                        if href not in csv_downloads:
                            csv_downloads.append(href)
                            
                print(f"Found {len(csv_downloads)} CSV download links for 2026.")
            except Exception as e:
                print(f"Error scraping PBH page: {e}")
            finally:
                browser.close()
                
        return csv_downloads

    def download_files(self, urls):
        """Downloads the files from the list of URLs using requests, falling back to Playwright if blocked."""
        downloaded_paths = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.DATASET_URL
        }
        
        for url in urls:
            filename = url.split("/")[-1]
            dest_path = os.path.join(self.download_dir, filename)
            
            if os.path.exists(dest_path):
                print(f"File already exists: {dest_path}. Skipping.")
                downloaded_paths.append(dest_path)
                continue
                
            print(f"Downloading: {filename} from {url}")
            try:
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    with open(dest_path, "wb") as f:
                        f.write(response.content)
                    print(f"Successfully downloaded: {dest_path}")
                    downloaded_paths.append(dest_path)
                else:
                    print(f"Failed to download via requests (HTTP {response.status_code}). Trying Playwright...")
                    # Fallback to Playwright download
                    self._download_via_playwright(url, dest_path)
                    if os.path.exists(dest_path):
                        downloaded_paths.append(dest_path)
            except Exception as e:
                print(f"Error downloading {filename}: {e}. Trying Playwright fallback...")
                try:
                    self._download_via_playwright(url, dest_path)
                    if os.path.exists(dest_path):
                        downloaded_paths.append(dest_path)
                except Exception as p_err:
                    print(f"Playwright fallback failed for {filename}: {p_err}")
                    
        return downloaded_paths

    def _download_via_playwright(self, url, dest_path):
        """Helper to download a file using Playwright's download handler to bypass WAF blocks."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ignore_https_errors=True
            )
            page = context.new_page()
            try:
                # Set up download listener
                with page.expect_download(timeout=60000) as download_info:
                    try:
                        page.goto(url, timeout=60000)
                    except Exception as e:
                        if "Download is starting" not in str(e):
                            raise e
                download = download_info.value
                download.save_as(dest_path)
                print(f"Successfully downloaded via Playwright: {dest_path}")
            except Exception as e:
                raise e
            finally:
                browser.close()

    def fetch_latest_iptu_url(self):
        """Launches Playwright to find the latest Cadastro Imobiliario Centro-Sul CSV download link."""
        url = "https://dados.pbh.gov.br/dataset/cadastro-imobiliario-regional-centro-sul"
        print(f"Opening PBH IPTU dataset page: {url}")
        csv_links = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                links = page.query_selector_all("a")
                for link in links:
                    href = link.get_attribute("href")
                    if href and ".csv" in href and "regional_centro_sul" in href:
                        csv_links.append(href)
            except Exception as e:
                print(f"Error scraping PBH IPTU page: {e}")
            finally:
                browser.close()
        
        if not csv_links:
            return None
            
        # Sort by date in filename (format: YYYYMMDD_regional_...)
        def get_date(u):
            filename = u.split("/")[-1]
            match = re.search(r"(\d{8})", filename)
            return match.group(1) if match else "00000000"
            
        csv_links.sort(key=get_date, reverse=True)
        return csv_links[0]

    def download_iptu(self, force=False):
        """Downloads the latest Centro-Sul IPTU CSV if it does not already exist."""
        url = self.fetch_latest_iptu_url()
        if not url:
            print("No IPTU CSV URL found.")
            return None
            
        filename = url.split("/")[-1]
        dest_path = os.path.join(self.iptu_dir, filename)
        
        if os.path.exists(dest_path) and not force:
            print(f"IPTU file already exists: {dest_path}. Skipping.")
            return dest_path
            
        print(f"Downloading IPTU: {filename} from {url}")
        try:
            self._download_via_playwright(url, dest_path)
        except Exception as e:
            print(f"Error downloading IPTU via Playwright: {e}")
            # Fallback to requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            try:
                response = requests.get(url, headers=headers, timeout=60)
                if response.status_code == 200:
                    with open(dest_path, "wb") as f:
                        f.write(response.content)
                    print(f"Successfully downloaded via requests: {dest_path}")
                else:
                    print(f"Requests download failed (HTTP {response.status_code})")
            except Exception as req_err:
                print(f"Requests fallback failed: {req_err}")
                
        return dest_path if os.path.exists(dest_path) else None

    def run(self):
        """Finds and downloads all 2026 ITBI CSV files, and downloads the latest IPTU CSV file."""
        urls = self.fetch_2026_csv_urls()
        itbi_paths = []
        if urls:
            itbi_paths = self.download_files(urls)
        else:
            print("No ITBI CSV URLs for 2026 found.")
            
        iptu_path = self.download_iptu()
        return itbi_paths

if __name__ == "__main__":
    client = PBHClient()
    client.run()
