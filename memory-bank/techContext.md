# Technical Context

## Technology Stack
1. **Core Technologies**
   - Python 3.11.4
   - JSON for data storage
   - Markdown for documentation
   - aiohttp for async I/O
   - Rich for terminal UI
   - TCIA API integration

2. **Development Tools**
   - Git for version control
   - VS Code/Cursor for development
   - PowerShell for scripting

## Development Setup
1. **Environment Requirements**
   - Python 3.11.4
   - Git
   - Text editor/IDE
   - Terminal/PowerShell
   - TCIA API access

2. **Project Structure**
   ```
   casewise/
   ├── scripts/          # Execution scripts
   │   ├── fc.py        # Main case fetcher
   │   ├── scanner.py   # Collection scanner
   │   └── nonivfc.py   # Non-interactive version of fc.py
   ├── utils/           # Utility functions
   ├── cases/           # Case management
   ├── data/            # Data storage
   ├── prompts/         # Prompt templates
   ├── grading/         # Grading system
   ├── speech_input/    # Speech processing
   ├── logs/            # System logs
   ├── docs/            # Documentation
   ├── memory-bank/     # Project memory
   └── cache/           # Study cache
   ```

## Dependencies
1. **Core Dependencies**
   - Python standard library
   - JSON processing
   - File handling
   - Logging
   - aiohttp==3.9.3
   - rich
   - pydicom
   - Pillow
   - openai
   - python-dotenv
   - tcia_utils
   - requests
   - questionary

2. **External Dependencies**
   - TCIA API
   - DICOM processing libraries
   - Image processing tools

## Technical Constraints
1. **System Requirements**
   - Windows 10/11
   - Python 3.11.4
   - Sufficient disk space
   - Adequate memory (1GB+ recommended)
   - TCIA API access

2. **Performance Requirements**
   - Asynchronous I/O operations
   - Parallel processing capabilities
   - Memory-efficient operations
   - Real-time feedback
   - Quick response times
   - Reliable operation
   - Efficient report detection

## Development Guidelines
1. **Code Style**
   - PEP 8 compliance
   - Clear documentation
   - Consistent formatting
   - Meaningful comments
   - Async/await patterns
   - Error handling

2. **Version Control**
   - Git workflow
   - Branch management
   - Commit messages
   - Code review
   - Feature branches

3. **Testing**
   - Unit testing
   - Integration testing
   - Test coverage
   - Performance testing
   - Memory usage monitoring

## Deployment
1. **Local Development**
   - Direct execution
   - Development server
   - Debug mode
   - Local testing
   - Performance profiling

2. **Production**
   - Performance optimized
   - Memory efficient
   - Error handling
   - Logging system
   - Security considerations

## Monitoring and Maintenance
1. **Logging**
   - System logs
   - Error logs
   - Performance logs
   - Memory usage logs
   - API response logs

2. **Maintenance**
   - Regular updates
   - Bug fixes
   - Performance optimization
   - Memory management
   - Security patches 