# Known Issues

## Purpose
This file tracks all known issues, bugs, and limitations in the project. It helps maintain awareness of current problems and their resolution status.

## How to Use This File
1. **Adding New Issues**
   - Add new issues at the top
   - Include date discovered
   - Document current status
   - Note any workarounds
   - Link to related commits/PRs

2. **Format**
   ```markdown
   ## [Date] - Issue Title
   **Status:** [Open/In Progress/Resolved]
   
   **Description:** Detailed description of the issue
   
   **Impact:** How this affects the system
   
   **Workaround:** Any temporary solutions
   
   **Resolution:** How it was fixed (if resolved)
   
   **Related:** Links to commits/PRs/issues
   ```

## Current Issues

### [2024-06-06] - Report Checking Performance
**Status:** In Progress

**Description:** 
- Initial implementation of report checking was inefficient
- Required fetching all patients before checking for reports
- No early exit for collections without reports

**Impact:**
- Slow performance for large collections
- Unnecessary API calls
- Poor user experience

**Workaround:**
- Implemented sampling approach
- Added early exit for collections without reports
- Added progress feedback

**Resolution:**
- Implemented async I/O
- Added parallel processing
- Enhanced memory management
- Added live timer display

**Related:**
- Performance Optimization v2 branch
- PR #XX

## Resolved Issues

### [2024-06-06] - Logger Initialization
**Status:** Resolved

**Description:**
- Logger variable not properly initialized in some functions
- Caused NameError in report checking

**Impact:**
- Script crashes
- No logging output

**Resolution:**
- Added logger parameter to functions
- Ensured proper initialization
- Updated all relevant calls

**Related:**
- Performance Optimization v2 branch
- PR #XX

## Notes
- Update status when issues are resolved
- Move resolved issues to the Resolved Issues section
- Include both critical and minor issues
- Document any patterns or recurring problems
- Link to relevant documentation or discussions 