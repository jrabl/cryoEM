#!/usr/bin/env python3
"""
------------------------cryoSPARC-plot-download-script--------------------------------------------------

Script for archiving refinement job data from CryoSPARC, in particular the png, pdf, xml, and txt files
that would have to be downloaded manually from the refinement job page. This script logs into cryoSPARC,
opens the page of the refinement job, and downloads the data files (txt, xml, bild) and plots (pdf, png)
and neatly organizes them into folders.

Usage:
    python cs_download.py <cryosparc_url> -u username -p password
    
Example:
    python cs_download.py "http://mars:42000/browse/P22-W11-J*#job(P22-J475)" -u username -p password
    
Julius Rabl, ETH Zurich, 251202   
--------------------------------------------------------------------------------------------------------
"""

import argparse
import base64
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException


def parse_job_url(url: str) -> tuple[str, str, str]:
    """
    Parse cryoSPARC URL to extract base URL, project ID, and job ID.
    
    Example URL: http://mars:42000/browse/P22-W11-J*#job(P22-J475)
    Returns: (base_url, project_id, job_id)
    """
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    
    # Extract job ID from fragment (e.g., "job(P22-J475)")
    fragment = parsed.fragment
    job_match = re.search(r'job\(([^)]+)\)', fragment)
    
    if not job_match:
        raise ValueError(f"Could not parse job ID from URL fragment: {fragment}")
    
    job_full = job_match.group(1)  # e.g., "P22-J475"
    
    # Split into project and job number
    parts = job_full.split('-')
    if len(parts) != 2:
        raise ValueError(f"Unexpected job ID format: {job_full}")
    
    project_id = parts[0]  # e.g., "P22"
    job_id = parts[1]      # e.g., "J475"
    
    return base_url, project_id, job_id


def create_output_directory(project_id: str, job_id: str) -> Path:
    """Create output directory structure for downloaded files."""
    output_dir = Path(f"{project_id}_{job_id}")
    
    # Create main directory and subdirectories
    subdirs = ['png', 'pdf', 'txt', 'xml', 'bild']
    
    output_dir.mkdir(exist_ok=True)
    for subdir in subdirs:
        (output_dir / subdir).mkdir(exist_ok=True)
    
    print(f"Created output directory: {output_dir}")
    return output_dir


def setup_driver(download_dir: Path = None, headless: bool = True) -> webdriver.Chrome:
    """Set up Chrome driver with optional download directory."""
    options = Options()
    if headless:
        options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    
    # Configure download directory if specified
    if download_dir:
        prefs = {
            'download.default_directory': str(download_dir.absolute()),
            'download.prompt_for_download': False,
            'download.directory_upgrade': True,
            'safebrowsing.enabled': True
        }
        options.add_experimental_option('prefs', prefs)
    
    driver = webdriver.Chrome(options=options)
    return driver


def wait_for_page_load(driver: webdriver.Chrome, timeout: int = 30):
    """Wait for the cryoSPARC page to fully load."""
    try:
        # Wait for the main content area to load
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".job-panel, .job-view, [class*='job'], .content"))
        )
        # Additional wait for dynamic content
        time.sleep(3)
    except TimeoutException:
        print("Warning: Page load timeout, proceeding anyway...")


def extract_file_links(driver: webdriver.Chrome, base_url: str) -> dict[str, list[str]]:
    """
    Extract all file links from the page organized by extension.
    
    CryoSPARC uses text formatting like [pdf], [png], [txt], [xml], [bild]
    to indicate downloadable files.
    """
    file_links = {
        'png': [],
        'pdf': [],
        'txt': [],
        'xml': [],
        'bild': []
    }
    
    # Find all anchor tags
    anchors = driver.find_elements(By.TAG_NAME, 'a')
    
    for anchor in anchors:
        try:
            href = anchor.get_attribute('href')
            if not href:
                continue
            
            # Get the link text
            link_text = anchor.text.strip()
            
            # Check if link text matches [ext] pattern (e.g., [pdf], [png])
            for ext in file_links.keys():
                # Match patterns like [pdf], [PNG], [pdf], etc.
                if re.match(rf'^\[\s*{ext}\s*\]$', link_text, re.IGNORECASE):
                    full_url = urljoin(base_url, href)
                    # Get the download filename from the download attribute
                    download_attr = anchor.get_attribute('download') or ''
                    if full_url not in file_links[ext]:
                        file_links[ext].append((full_url, download_attr))
                        print(f"    Found [{ext}] link: {full_url} ({download_attr})")
                    break
            
            # Also check if URL ends with extension (fallback)
            href_lower = href.lower()
            for ext in file_links.keys():
                if href_lower.endswith(f'.{ext}'):
                    full_url = urljoin(base_url, href)
                    if full_url not in file_links[ext]:
                        file_links[ext].append(full_url)
                    break
        except Exception:
            continue
    
    return file_links


def download_file_with_selenium(driver: webdriver.Chrome, url: str, output_path: Path) -> bool:
    """Download a file using Selenium's authenticated session."""
    # Skip malformed URLs (contain spaces, quotes, or HTML)
    if ' ' in url or '"' in url or '<' in url or '>' in url:
        print(f"  Skipping malformed URL: {url[:80]}...")
        return False
    
    try:
        # Use JavaScript fetch with the browser's cookies
        script = """
        async function downloadFile(url) {
            const response = await fetch(url, {credentials: 'include'});
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const blob = await response.blob();
            const reader = new FileReader();
            return new Promise((resolve, reject) => {
                reader.onload = () => resolve(reader.result.split(',')[1]);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
        }
        return downloadFile(arguments[0]);
        """
        
        # Execute and get base64 encoded content
        result = driver.execute_script(script, url)
        
        if result:
            content = base64.b64decode(result)
            with open(output_path, 'wb') as f:
                f.write(content)
            print(f"  Downloaded: {output_path.name}")
            return True
        else:
            print(f"  Failed to download {url}: No content returned")
            return False
            
    except Exception as e:
        print(f"  Failed to download {url}: {e}")
        return False


def download_files_by_extension(file_links: dict[str, list], output_dir: Path, 
                                driver: webdriver.Chrome):
    """Download all files organized by extension."""
    for ext, items in file_links.items():
        if not items:
            print(f"\nNo {ext.upper()} files found.")
            continue
        
        print(f"\nDownloading {len(items)} {ext.upper()} files...")
        ext_dir = output_dir / ext
        
        for idx, item in enumerate(items):
            # Handle both tuple (url, filename) and plain url formats
            if isinstance(item, tuple):
                url, download_name = item
            else:
                url = item
                download_name = ''
            
            # Use download attribute filename if available, otherwise extract from URL
            if download_name:
                # Clean up double extensions like .png.png -> .png
                filename = download_name
                if filename.endswith(f'.{ext}.{ext}'):
                    filename = filename[:-len(f'.{ext}')]
            else:
                filename = os.path.basename(urlparse(url).path)
                if not filename or len(filename) < 3:
                    filename = f"file_{idx}.{ext}"
                elif not filename.endswith(f'.{ext}'):
                    filename = f"{filename}.{ext}"
            
            output_path = ext_dir / filename
            
            # Handle duplicate filenames
            counter = 1
            while output_path.exists():
                name, extension = os.path.splitext(filename)
                output_path = ext_dir / f"{name}_{counter}{extension}"
                counter += 1
            
            download_file_with_selenium(driver, url, output_path)


def login_to_cryosparc(driver: webdriver.Chrome, email: str, password: str, timeout: int = 30):
    """Log in to cryoSPARC using provided credentials."""
    print("Logging in to cryoSPARC...")
    
    try:
        # Wait for login form
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, 'email-address'))
        )
        
        # Fill in credentials
        email_field = driver.find_element(By.ID, 'email-address')
        password_field = driver.find_element(By.ID, 'password')
        
        email_field.clear()
        email_field.send_keys(email)
        
        password_field.clear()
        password_field.send_keys(password)
        
        # Submit the form
        submit_button = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        
        # Wait for button to be enabled
        WebDriverWait(driver, 5).until(
            lambda d: not submit_button.get_attribute('disabled')
        )
        
        submit_button.click()
        
        # Wait for login to complete (page should change)
        time.sleep(3)
        
        # Check if still on login page
        if 'login' in driver.current_url.lower() or 'Log in' in driver.page_source:
            # Check for error message
            try:
                error = driver.find_element(By.CSS_SELECTOR, '.error, [class*="error"]')
                if error.text:
                    print(f"Login error: {error.text}")
            except NoSuchElementException:
                pass
            return False
        
        print("Login successful!")
        return True
        
    except TimeoutException:
        print("Login form not found - may already be logged in or page structure changed")
        return True  # Assume we can proceed
    except Exception as e:
        print(f"Login failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Download data from cryoSPARC refinement job page',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python cs_download.py "http://mars:42000/browse/P22-W11-J*#job(P22-J475)" -u user@example.com -p password
    python cs_download.py "http://mars:42000/browse/P22-W11-J*#job(P22-J478)" -u user@example.com -p password
        """
    )
    parser.add_argument('url', help='cryoSPARC job URL')
    parser.add_argument('-u', '--user', '--email', dest='email',
                        help='cryoSPARC login email')
    parser.add_argument('-p', '--password', 
                        help='cryoSPARC login password')
    parser.add_argument('--timeout', type=int, default=30, 
                        help='Page load timeout in seconds (default: 30)')
    parser.add_argument('--no-headless', action='store_true',
                        help='Run browser in visible mode (for debugging)')
    
    args = parser.parse_args()
    
    print(f"CryoSPARC Job Data Downloader")
    print("=" * 50)
    
    # Parse the URL
    try:
        base_url, project_id, job_id = parse_job_url(args.url)
        print(f"Base URL: {base_url}")
        print(f"Project: {project_id}")
        print(f"Job: {job_id}")
    except ValueError as e:
        print(f"Error parsing URL: {e}")
        sys.exit(1)
    
    # Create output directory
    output_dir = create_output_directory(project_id, job_id)
    
    # Set up Selenium driver
    print("\nStarting browser...")
    driver = None
    
    try:
        driver = setup_driver(
            download_dir=output_dir, 
            headless=not args.no_headless
        )
        
        # Navigate to the page
        print(f"Loading page: {args.url}")
        driver.get(args.url)
        
        # Check if login is required
        time.sleep(2)
        if 'login' in driver.current_url.lower() or 'Log in' in driver.page_source:
            if not args.email or not args.password:
                print("\nLogin required! Please provide credentials:")
                print("  python cs_download.py <url> -u <email> -p <password>")
                sys.exit(1)
            
            if not login_to_cryosparc(driver, args.email, args.password, args.timeout):
                print("Failed to log in to cryoSPARC")
                sys.exit(1)
            
            # Navigate to the job page after login
            print(f"Navigating to job page: {args.url}")
            driver.get(args.url)
        
        # Wait for page to load
        wait_for_page_load(driver, args.timeout)
        print("Page loaded.")
        
        # Debug: save page source
        debug_file = output_dir / 'debug_page.html'
        with open(debug_file, 'w') as f:
            f.write(driver.page_source)
        print(f"Saved page source to: {debug_file}")
        
        # Extract file links
        print("\nSearching for downloadable files...")
        file_links = extract_file_links(driver, base_url)
        
        for ext, urls in file_links.items():
            print(f"  Found {len(urls)} {ext.upper()} files")
        
        # Download files
        print("\n" + "=" * 50)
        print("Downloading files...")
        
        # Download files by extension (using Selenium for authentication)
        download_files_by_extension(file_links, output_dir, driver)
        
        
        print("\n" + "=" * 50)
        print(f"Download complete! Files saved to: {output_dir}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    finally:
        if driver:
            driver.quit()


if __name__ == '__main__':
    main()

