"""Register tools and describe them to the model.

To build:
- a tool = name, description, JSON schema for arguments, the function to run
- validate arguments against the schema before running
- return results (or errors) as text the model can read; clip long output
"""
