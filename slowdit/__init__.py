"""slowdit — an MCP server for executing untrusted code in an isolated environment.

slowdit is the executor: it runs a piece of code (typically a repository's
test suite at a given commit) in a throwaway container built from a
caller-supplied image, and returns structured results. It does not decide
what to run or what "passing" means — that is the caller's job.

See README.md for the contract.
"""

__version__ = "0.1.0"
