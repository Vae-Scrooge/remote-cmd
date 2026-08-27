import re

with open('CHANGELOG.md', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all version sections - more robust pattern
sections = re.split(r'^## \[', content, flags=re.MULTILINE)

for section in sections:
    if not section.strip():
        continue
    # Extract version
    first_line = section.strip().split('\n')[0]
    version_match = re.match(r'([^\]]+)\]', first_line)
    if not version_match:
        continue
    version = version_match.group(1)

    # Skip Unreleased
    if version == 'Unreleased':
        continue

    # Get the content (everything after the version line)
    lines = section.strip().split('\n')
    notes = '\n'.join(lines[1:]).strip()

    # Create release notes file
    filename = f'release_notes/{version}.md'
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(notes)
    print(f'Created {filename} ({len(notes)} chars)')