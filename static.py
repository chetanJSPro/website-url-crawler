import asyncio
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright
import json
import time

visited = set()
sitemap = []

async def wait_for_dynamic_content(page, max_wait=30):
    """Wait for dynamic content to load by monitoring DOM changes"""
    start_time = time.time()
    previous_link_count = 0
    stable_count = 0
    
    while time.time() - start_time < max_wait:
        # Wait for any pending network requests
        try:
            await page.wait_for_load_state('networkidle', timeout=5000)
        except:
            pass
        
        # Count current links
        current_link_count = await page.evaluate('document.querySelectorAll("a[href]").length')
        
        # Check if link count has stabilized
        if current_link_count == previous_link_count:
            stable_count += 1
            if stable_count >= 3:  # Content seems stable
                break
        else:
            stable_count = 0
            previous_link_count = current_link_count
        
        await asyncio.sleep(1)

async def smart_scroll(page):
    """Enhanced scrolling for dynamic content loading"""
    await page.evaluate("""
        async () => {
            const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
            
            // Scroll to trigger lazy loading
            const scrollStep = 300;
            const scrollDelay = 500;
            let lastHeight = document.body.scrollHeight;
            let currentHeight = 0;
            
            while (currentHeight < lastHeight) {
                window.scrollBy(0, scrollStep);
                currentHeight += scrollStep;
                await delay(scrollDelay);
                
                // Check if new content loaded
                const newHeight = document.body.scrollHeight;
                if (newHeight > lastHeight) {
                    lastHeight = newHeight;
                }
            }
            
            // Scroll back to top
            window.scrollTo(0, 0);
            await delay(1000);
        }
    """)

async def extract_links_comprehensive(page, base_url):
    """Extract links with better filtering for SPAs"""
    # Wait a bit more for any remaining dynamic content
    await asyncio.sleep(2)
    
    links = await page.evaluate('''
        () => {
            const links = Array.from(document.querySelectorAll('a[href]'))
                .map(el => el.href)
                .filter(href => {
                    if (!href || href === '#' || href === 'javascript:void(0)') return false;
                    if (href.includes('mailto:') || href.includes('tel:')) return false;
                    if (href.includes('#') && !href.split('#')[0]) return false; // Pure hash links
                    return true;
                });
            
            // Also look for data-* attributes that might contain URLs (common in SPAs)
            const dataLinks = Array.from(document.querySelectorAll('[data-href], [data-url], [data-link]'))
                .map(el => el.getAttribute('data-href') || el.getAttribute('data-url') || el.getAttribute('data-link'))
                .filter(href => href && href.startsWith('http'));
            
            return [...new Set([...links, ...dataLinks])];
        }
    ''')
    
    # Normalize and filter links
    normalized_links = []
    for link in links:
        try:
            # Handle relative URLs
            if link.startswith('/'):
                link = urljoin(base_url, link)
            elif not link.startswith('http'):
                continue
            
            # Remove fragments
            if '#' in link:
                link = link.split('#')[0]
            
            # Remove trailing slashes for consistency
            link = link.rstrip('/')
            
            normalized_links.append(link)
        except:
            continue
    
    return list(set(normalized_links))

async def handle_spa_navigation(page, url):
    """Handle Single Page Application navigation"""
    try:
        # For SPAs, try clicking navigation instead of direct navigation
        current_url = page.url
        if current_url != url:
            # Try to find and click the link
            link_selector = f'a[href*="{url.replace(current_url.split("/")[0] + "//" + current_url.split("/")[2], "")}"]'
            try:
                await page.click(link_selector, timeout=5000)
                await page.wait_for_load_state('networkidle', timeout=10000)
                return True
            except:
                # Fall back to direct navigation
                await page.goto(url, timeout=60000, wait_until='networkidle')
                return True
    except:
        return False
    return True

async def crawl(context, base_url, current_url, depth=0, max_depth=3):
    if (current_url in visited or 
        not current_url.startswith(base_url) or 
        depth > max_depth):
        return
    
    print(f"{'  '*depth}Crawling: {current_url} (depth: {depth})")
    visited.add(current_url)
    
    page = await context.new_page()
    
    # Set longer timeouts for dynamic content
    page.set_default_timeout(60000)
    page.set_default_navigation_timeout(60000)
    
    try:
        # Navigate to page
        await page.goto(current_url, wait_until='networkidle', timeout=60000)
        
        # Wait for dynamic content
        await wait_for_dynamic_content(page)
        
        # Enhanced scrolling for lazy-loaded content
        await smart_scroll(page)
        
        # Additional wait for any final rendering
        await asyncio.sleep(3)
        
        # Extract page info
        page_info = await page.evaluate('''
            () => ({
                title: document.title,
                description: document.querySelector('meta[name="description"]')?.content || '',
                url: window.location.href
            })
        ''')
        
        sitemap.append({
            "url": current_url,
            "title": page_info.get('title', ''),
            "description": page_info.get('description', ''),
            "depth": depth
        })
        
        # Extract links
        links = await extract_links_comprehensive(page, base_url)
        
        print(f"{'  '*depth}Found {len(links)} links")
        
        # Crawl child pages
        for link in links:
            if link not in visited and base_url in link:
                await crawl(context, base_url, link, depth + 1, max_depth)
    
    except Exception as e:
        print(f"[ERROR] {current_url}: {e}")
        sitemap.append({
            "url": current_url,
            "title": "Error loading page",
            "description": str(e),
            "depth": depth,
            "error": True
        })
    
    finally:
        await page.close()

async def main(start_url, max_depth=3):
    parsed_url = urlparse(start_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    print(f"Starting crawl of: {start_url}")
    print(f"Base URL: {base_url}")
    print(f"Max depth: {max_depth}")
    print("-" * 50)
    
    async with async_playwright() as p:
        # Use a more realistic browser context
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
        
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        
        # Enable JavaScript
        await context.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")
        
        await crawl(context, base_url, start_url, max_depth=max_depth)
        await browser.close()
    
    # Save results
    output_file = "sitemap.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(sitemap, f, indent=2, ensure_ascii=False)
    
    print(f"\nCrawl completed!")
    print(f"Total pages crawled: {len(sitemap)}")
    print(f"Results saved to: {output_file}")
    
    # Print summary
    successful_pages = [p for p in sitemap if not p.get('error', False)]
    error_pages = [p for p in sitemap if p.get('error', False)]
    
    print(f"Successful: {len(successful_pages)}")
    print(f"Errors: {len(error_pages)}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python dynamic_crawler.py <URL> [max_depth]")
        print("Example: python dynamic_crawler.py https://your-gatsby-site.com 2")
    else:
        url = sys.argv[1]
        max_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        asyncio.run(main(url, max_depth))