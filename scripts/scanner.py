import subprocess
import json
import asyncio
import argparse
from pathlib import Path
from subspecialty_map import subspecialty_map
from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from rich.panel import Panel
from rich import print as rprint
import sys

CACHE_FILE = Path("scan_cache.json")
console = Console()

def parse_args():
    parser = argparse.ArgumentParser(description="TCIA Collection Scanner")
    parser.add_argument("--collection", help="Scan a specific collection")
    parser.add_argument("--subspecialty", help="Scan all collections in a subspecialty")
    parser.add_argument("--all", action="store_true", help="Scan all collections")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--refresh", action="store_true", help="Force refresh and overwrite cache results")
    return parser.parse_args()

def load_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def get_all_collections():
    """Get all collections from subspecialty_map."""
    all_collections = []
    for collections in subspecialty_map.values():
        all_collections.extend(collections)
    return sorted(all_collections)

async def scan_collection(collection_name, cache, subspecialty=None, debug=False, refresh=False):
    # Check cache first
    if subspecialty and collection_name in cache.get(subspecialty, {}) and not refresh:
        console.print(f"[green]Cached:[/green] {collection_name} (has_reports: {cache[subspecialty][collection_name]['has_reports']})")
        return cache[subspecialty][collection_name]['has_reports']
    
    console.print(f"[yellow]Scanning collection:[/yellow] {collection_name}")
    try:
        # Use the current python executable (venv)
        python_exe = sys.executable
        cmd = [python_exe, "scripts/nonivfc.py", "--collection", collection_name, "--report-required"]
        if debug:
            console.print(f"[blue]Running command:[/blue] {' '.join(cmd)}")
        
        # Run nonivfc.py with asyncio
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        if debug:
            console.print("[blue]Waiting for nonivfc.py output...[blue]")
        
        stdout, stderr = await process.communicate()
        
        if debug:
            console.print(f"[blue]Raw stdout:[/blue] {stdout.decode()}")
            if stderr:
                console.print(f"[blue]Raw stderr:[/blue] {stderr.decode()}")
        
        # Check return code - 0 means has reports, 1 means no reports
        has_reports = process.returncode == 0
        
        if subspecialty:
            cache.setdefault(subspecialty, {})[collection_name] = {"has_reports": has_reports}
            save_cache(cache)
        
        console.print(f"[green]Result:[/green] {collection_name} (has_reports: {has_reports})")
        return has_reports
        
    except Exception as e:
        console.print(f"[red]Error scanning {collection_name}: {str(e)}[/red]")
        return False

async def scan_subspecialty(subspecialty, cache, debug=False, refresh=False):
    """Scan all collections in a given subspecialty."""
    if subspecialty not in subspecialty_map:
        console.print(f"[red]Subspecialty '{subspecialty}' not found.[/red]")
        return
    collections = subspecialty_map[subspecialty]
    console.print(f"[yellow]Scanning {len(collections)} collections in {subspecialty}...[yellow]")
    for collection in collections:
        await scan_collection(collection, cache, subspecialty, debug, refresh)

async def scan_all(cache, debug=False, refresh=False):
    """Scan all collections across all subspecialties."""
    for subspecialty in subspecialty_map:
        await scan_subspecialty(subspecialty, cache, debug, refresh)

def show_menu():
    console.clear()
    console.print(Panel.fit(
        "[bold blue]TCIA Collection Scanner[/bold blue]\n"
        "[bold]1.[/bold] Scan by subspecialty\n"
        "[bold]2.[/bold] Scan all collections\n"
        "[bold]3.[/bold] Scan a single collection\n"
        "[bold]4.[/bold] [red]Exit[/red]",
        title="Menu",
        border_style="blue"
    ))
    return Prompt.ask("Enter your choice", choices=["1", "2", "3", "4"])

def show_subspecialty_menu():
    console.print("\n[bold]Available subspecialties:[/bold]")
    subspecialties = list(subspecialty_map.keys())
    for i, subspecialty in enumerate(subspecialties, 1):
        console.print(f"[bold]{i}.[/bold] {subspecialty}")
    console.print(f"[bold]{len(subspecialties) + 1}.[/bold] [cyan]← Back to main menu[/cyan]")
    
    while True:
        try:
            choice = IntPrompt.ask("\nSelect a subspecialty (enter number)", default=1)
            if 1 <= choice <= len(subspecialties):
                return subspecialties[choice - 1]
            elif choice == len(subspecialties) + 1:
                return None
            console.print("[red]Invalid selection. Please try again.[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number.[/red]")

def show_collection_menu():
    console.print("\n[bold]Available collections:[/bold]")
    collections = get_all_collections()
    for i, collection in enumerate(collections, 1):
        console.print(f"[bold]{i}.[/bold] {collection}")
    console.print(f"[bold]{len(collections) + 1}.[/bold] [cyan]← Back to main menu[/cyan]")
    
    while True:
        try:
            choice = IntPrompt.ask("\nSelect a collection (enter number)", default=1)
            if 1 <= choice <= len(collections):
                return collections[choice - 1]
            elif choice == len(collections) + 1:
                return None
            console.print("[red]Invalid selection. Please try again.[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number.[/red]")

async def interactive_mode(debug=False, refresh=False):
    cache = load_cache()
    while True:
        choice = show_menu()
        
        if choice == "1":
            selected = show_subspecialty_menu()
            if selected:
                await scan_subspecialty(selected, cache, debug, refresh)
                input("\nPress Enter to continue...")
        elif choice == "2":
            await scan_all(cache, debug, refresh)
            input("\nPress Enter to continue...")
        elif choice == "3":
            selected = show_collection_menu()
            if selected:
                subspecialty = next((s for s, colls in subspecialty_map.items() if selected in colls), None)
                await scan_collection(selected, cache, subspecialty, debug, refresh)
                input("\nPress Enter to continue...")
        elif choice == "4":
            console.print("[cyan]Goodbye![cyan]")
            break
        else:
            console.print("[red]Invalid choice.[/red]")
            input("\nPress Enter to continue...")

async def main():
    args = parse_args()
    cache = load_cache()
    
    if args.collection:
        subspecialty = next((s for s, colls in subspecialty_map.items() if args.collection in colls), None)
        await scan_collection(args.collection, cache, subspecialty, args.debug, args.refresh)
    elif args.subspecialty:
        await scan_subspecialty(args.subspecialty, cache, args.debug, args.refresh)
    elif args.all:
        await scan_all(cache, args.debug, args.refresh)
    else:
        await interactive_mode(args.debug, args.refresh)

if __name__ == "__main__":
    asyncio.run(main()) 