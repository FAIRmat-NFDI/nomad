***Note:** This is a general README file for NOMAD parsers, consult the README of specific parser projects for more detailed information!*

$preamble$

This is a NOMAD parser for [$codeLabel$]($codeUrl$). It will read $codeLabel$ input and
output files and provide all information in NOMAD's unified Metainfo based Archive format.

## Preparing code input and output file for uploading to NOMAD

An *upload* is basically a directory structure with files. If you have all the files locally
you can just upload everything as a `.zip` or `.tar.gz` file in a single step. While the upload is
in the *staging area* (i.e. before it is published) you can also easily add or remove files in the
directory tree via the web interface. NOMAD will automatically try to choose the right parser
for you files.

For each parser there is one type of file that the respective parser can recognize. We call
these files *mainfiles*. For each mainfile that NOMAD discovers it will create an *entry*
in the database, which users can search, view, and download. NOMAD will consider all files
in the same directory as *auxiliary files* that also are associated with that entry. Parsers
might also read information from these auxiliary files. This way you can add more files
to an entry, even if the respective parser/code might not use them. However, we strongly
recommend to not have multiple mainfiles in the same directory. For CMS calculations, we
recommend having a separate directory for each code run.

For $codeLabel$ please provide at least the files from this table, if applicable
(remember that you always can provide additional files if you want):

$tableOfFiles$

Go to the [NOMAD upload page](https://nomad-lab.eu/prod/rae/gui/uploads) to upload files
or find instructions about how to upload files from the command line.

## Using the parser

You can use NOMAD's parsers and normalizers locally on your computer. You need to install
NOMAD's pypi package:

```
pip install nomad-lab
```

To parse code input/output from the command line, you can use NOMAD's command line
interface (CLI) and print the processing results output to stdout:

```
nomad parse <path-to-file>
```

To parse a file in Python, you can program something like this:
```python
import sys
from nomad.cli.parse import parse, normalize_all

# match and run the parser
archive = parse(sys.argv[1])
# run all normalizers
normalize_all(archive)

# get the 'main section' section_run as a metainfo object
section_run = archive.section_run[0]

# get the same data as JSON serializable Python dict
python_dict = section_run.m_to_dict()
```

## Developing the parser

Create a virtual environment to install the parser in development mode:

```
pip install virtualenv
virtualenv -p `which python3` .pyenv
source .pyenv/bin/activate
```

Install NOMAD's pypi package:

```
pip install nomad-lab
```

Clone the parser project and install it in development mode:

```
git clone $parserGitUrl$ $gitPath$
pip install -e $gitPath$
```

Running the parser now, will use the parser's Python code from the clone project.

$parserSpecific$
