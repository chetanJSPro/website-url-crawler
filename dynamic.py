import asyncio
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright
import json
import time
import re

visited = set()
sitemap = []

async def wait_for_spa_content(page, timeout=45):
    """Enhanced waiting for SPA content with multiple strategies"""
    print("    Waiting for SPA content to load...")
    start_time = time.time()
    
    strategies = [
        # Strategy 1: Wait for React/JS frameworks to finish rendering
        lambda: page.wait_for_function(
            "() => window.React || window.__REACT_DEVTOOLS_GLOBAL_HOOK__ || document.readyState === 'complete'",
            timeout=10000
        ),
        
        # Strategy 2: Wait for common SPA indicators
        lambda: page.wait_for_function(
            """() => {
                const indicators = [
                    document.querySelector('[data-reactroot]'),
                    document.querySelector('[data-react-helmet]'),
                    document.querySelector('.App'),
                    document.querySelector('#root'),
                    document.querySelector('#app'),
                    document.querySelector('main'),
                    document.querySelector('[role="main"]')
                ];
                return indicators.some(el => el && el.children.length > 0);
            }""",
            timeout=10000
        ),
        
        # Strategy 3: Wait for navigation/content elements
        lambda: page.wait_for_function(
            """() => {
                const navElements = document.querySelectorAll('nav, .nav, .navigation, .menu, header, .header');
                const contentElements = document.querySelectorAll('main, .main, .content, article, section');
                return navElements.length > 0 && contentElements.length > 0;
            }""",
            timeout=10000
        )
    ]
    
    # Try each strategy
    for i, strategy in enumerate(strategies):
        try:
            await strategy()
            print(f"    Strategy {i+1} succeeded")
            break
        except Exception as e:
            print(f"    Strategy {i+1} failed: {str(e)[:50]}...")
            continue
    
    # Additional wait for any lazy content
    await asyncio.sleep(3)
    
    # Wait for network to be quiet
    try:
        await page.wait_for_load_state('networkidle', timeout=15000)
    except:
        pass

async def trigger_spa_navigation(page):
    """Trigger SPA navigation by simulating user interactions"""
    try:
        # Scroll to trigger any intersection observers
        await page.evaluate("""
            async () => {
                const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
                
                // Scroll down slowly to trigger lazy loading
                for (let i = 0; i < 5; i++) {
                    window.scrollBy(0, window.innerHeight / 2);
                    await delay(500);
                }
                
                // Scroll back to top
                window.scrollTo(0, 0);
                await delay(1000);
                
                // Try to trigger any hover effects or dynamic menus
                const interactiveElements = document.querySelectorAll('nav a, .menu a, button, [role="button"]');
                for (let el of interactiveElements) {
                    el.dispatchEvent(new Event('mouseenter'));
                    el.dispatchEvent(new Event('focus'));
                    await delay(100);
                }
            }
        """)
    except Exception as e:
        print(f"    Navigation trigger failed: {e}")

async def extract_spa_links(page, base_url):
    """Enhanced link extraction for SPAs"""
    await asyncio.sleep(2)  # Wait for any final rendering
    
    links = await page.evaluate(f'''
        () => {{
            const baseUrl = "{base_url}";
            const currentPath = window.location.pathname;
            const links = new Set();
            
            // Standard href links
            document.querySelectorAll('a[href]').forEach(el => {{
                const href = el.getAttribute('href');
                if (href && href !== '#' && !href.startsWith('mailto:') && !href.startsWith('tel:')) {{
                    links.add(href);
                }}
            }});
            
            // React Router style links (data attributes)
            document.querySelectorAll('[data-href], [data-to], [data-url]').forEach(el => {{
                const href = el.getAttribute('data-href') || el.getAttribute('data-to') || el.getAttribute('data-url');
                if (href) links.add(href);
            }});
            
            // Look for navigation items with onclick handlers
            document.querySelectorAll('nav *[onclick], .nav *[onclick], .menu *[onclick]').forEach(el => {{
                const onclick = el.getAttribute('onclick');
                if (onclick) {{
                    const pathMatch = onclick.match(/['"](\/[^'"]*)['"]/);
                    if (pathMatch) links.add(pathMatch[1]);
                }}
            }});
            
            // Check for programmatic navigation patterns
            const scripts = Array.from(document.querySelectorAll('script')).map(s => s.textContent);
            const allScriptText = scripts.join(' ');
            const routePaths = allScriptText.match(/['"](\/[a-zA-Z0-9\-_\/]*)['"]/g) || [];
            routePaths.forEach(path => {{
                const cleanPath = path.replace(/['"]/g, '');
                if (cleanPath.startsWith('/') && cleanPath.length > 1 && !cleanPath.includes(' ')) {{
                    links.add(cleanPath);
                }}
            }});
            
            // Convert to absolute URLs
            const absoluteLinks = Array.from(links).map(link => {{
                if (link.startsWith('http')) return link;
                if (link.startsWith('/')) return baseUrl + link;
                return baseUrl + '/' + link;
            }}).filter(link => {{
                return link.startsWith(baseUrl) && 
                       !link.includes('#') && 
                       !link.includes('?') &&
                       link !== baseUrl &&
                       link !== baseUrl + '/';
            }});
            
            return [...new Set(absoluteLinks)];
        }}
    ''')
    
    # Also try to find links mentioned in the page source/JavaScript
    try:
        page_content = await page.content()
        # Look for route definitions in JavaScript
        route_patterns = [
            r'["\']/([\w\-/]+)["\']',  # Path strings
            r'path:\s*["\']/([\w\-/]+)["\']',  # Route definitions
            r'to=["\']/([\w\-/]+)["\']',  # React Router to prop
        ]
        
        for pattern in route_patterns:
            matches = re.findall(pattern, page_content)
            for match in matches:
                if match and not match.startswith('http'):
                    potential_url = f"{base_url}/{match}".replace('//', '/')
                    if potential_url.startswith(base_url):
                        links.append(potential_url)
    except:
        pass
    
    return list(set(links))

async def navigate_spa_route(page, url):
    """Handle SPA route navigation"""
    try:
        current_url = page.url.rstrip('/')
        target_url = url.rstrip('/')
        
        if current_url == target_url:
            return True
            
        print(f"    Navigating from {current_url} to {target_url}")
        
        # Try direct navigation first
        try:
            await page.goto(url, wait_until='networkidle', timeout=30000)
            await wait_for_spa_content(page)
            return True
        except Exception as e:
            print(f"    Direct navigation failed: {e}")
        
        # Try clicking a link if direct navigation fails
        try:
            # Look for a link that matches this URL
            link_clicked = await page.evaluate(f'''
                () => {{
                    const targetUrl = "{url}";
                    const targetPath = new URL(targetUrl).pathname;
                    
                    const links = document.querySelectorAll('a[href], [data-href], [data-to]');
                    for (let link of links) {{
                        const href = link.getAttribute('href') || link.getAttribute('data-href') || link.getAttribute('data-to');
                        if (href && (href === targetPath || href === targetUrl)) {{
                            link.click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            ''')
            
            if link_clicked:
                await asyncio.sleep(2)
                await wait_for_spa_content(page)
                return True
                
        except Exception as e:
            print(f"    Link clicking failed: {e}")
        
        return False
        
    except Exception as e:
        print(f"    Navigation error: {e}")
        return False

async def crawl_spa(context, base_url, current_url, depth=0, max_depth=2):
    if (current_url in visited or 
        not current_url.startswith(base_url) or 
        depth > max_depth):
        return
    
    print(f"{'  '*depth}📍 Crawling SPA: {current_url} (depth: {depth})")
    visited.add(current_url)
    
    page = await context.new_page()
    
    # Configure page for SPA
    page.set_default_timeout(45000)
    page.set_default_navigation_timeout(45000)
    
    # Add console logging for debugging
    page.on("console", lambda msg: print(f"    🔍 Console: {msg.text}") if "error" in msg.text.lower() else None)
    
    try:
        # Navigate to the page
        success = await navigate_spa_route(page, current_url)
        if not success:
            raise Exception("Failed to navigate to SPA route")
        
        # Trigger any dynamic content loading
        await trigger_spa_navigation(page)
        
        # Wait a bit more for everything to settle
        await asyncio.sleep(2)
        
        # Extract page information
        page_info = await page.evaluate('''
            () => {
                // Try multiple selectors for title
                const title = document.title || 
                            document.querySelector('h1')?.textContent ||
                            document.querySelector('[data-testid="title"]')?.textContent ||
                            document.querySelector('.title')?.textContent ||
                            'No title found';
                
                // Try multiple selectors for description
                const description = document.querySelector('meta[name="description"]')?.content ||
                                  document.querySelector('meta[property="og:description"]')?.content ||
                                  document.querySelector('.description')?.textContent ||
                                  document.querySelector('p')?.textContent?.substring(0, 160) ||
                                  '';
                
                return {
                    title: title.trim(),
                    description: description.trim(),
                    url: window.location.href,
                    hasContent: document.body.innerText.trim().length > 100
                };
            }
        ''')
        
        sitemap.append({
            "url": current_url,
            "title": page_info.get('title', 'No title'),
            "description": page_info.get('description', ''),
            "depth": depth,
            "hasContent": page_info.get('hasContent', False),
            "timestamp": time.time()
        })
        
        # Extract links for further crawling
        links = await extract_spa_links(page, base_url)
        unique_links = [link for link in set(links) if link not in visited]
        
        print(f"{'  '*depth}🔗 Found {len(unique_links)} new links to crawl")
        
        # Crawl child pages
        for link in unique_links[:10]:  # Limit to prevent infinite crawling
            if link not in visited and base_url in link:
                await crawl_spa(context, base_url, link, depth + 1, max_depth)
    
    except Exception as e:
        print(f"{'  '*depth}❌ Error crawling {current_url}: {e}")
        sitemap.append({
            "url": current_url,
            "title": "Error loading page",
            "description": str(e),
            "depth": depth,
            "error": True,
            "timestamp": time.time()
        })
    
    finally:
        await page.close()

async def main(start_url, max_depth=2):
    parsed_url = urlparse(start_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    print(f"🚀 Starting SPA crawl of: {start_url}")
    print(f"🎯 Base URL: {base_url}")
    print(f"📊 Max depth: {max_depth}")
    print("=" * 60)
    
    async with async_playwright() as p:
        # Launch browser with SPA-friendly settings
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-extensions',
                '--disable-gpu',
                '--enable-features=NetworkService,NetworkServiceLogging',
                '--disable-features=TranslateUI,VizDisplayCompositor'
            ]
        )
        
        # Create context optimized for SPAs
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True,
            java_script_enabled=True
        )
        
        # Enable request interception for debugging
        # await context.route("**/*", lambda route: route.continue_())
        
        await crawl_spa(context, base_url, start_url, max_depth=max_depth)
        await browser.close()
    
    # Save results
    output_file = "spa_sitemap.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(sitemap, f, indent=2, ensure_ascii=False)
    
    print("\n" + "=" * 60)
    print(f"✅ SPA crawl completed!")
    print(f"📁 Total pages discovered: {len(sitemap)}")
    print(f"💾 Results saved to: {output_file}")
    
    # Print summary
    successful_pages = [p for p in sitemap if not p.get('error', False)]
    error_pages = [p for p in sitemap if p.get('error', False)]
    pages_with_content = [p for p in successful_pages if p.get('hasContent', False)]
    
    print(f"✅ Successful: {len(successful_pages)}")
    print(f"📄 With content: {len(pages_with_content)}")
    print(f"❌ Errors: {len(error_pages)}")
    
    print("\n📋 Pages found:")
    for page in successful_pages[:10]:  # Show first 10 pages
        content_indicator = "📄" if page.get('hasContent') else "📋"
        print(f"  {content_indicator} {page['url']} - {page['title']}")
    
    if len(successful_pages) > 10:
        print(f"  ... and {len(successful_pages) - 10} more pages")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python spa_crawler.py <URL> [max_depth]")
        print("Example: python spa_crawler.py https://chetan.pro 2")
    else:
        url = sys.argv[1]
        max_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 2
        asyncio.run(main(url, max_depth))