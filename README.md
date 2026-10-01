[![pipeline status](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/badges/develop/pipeline.svg)](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/pipelines)
[![backend coverage report](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/badges/develop/coverage.svg?job=python+coverage+report&key_text=backend+coverage&key_width=130)](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/jobs/artifacts/develop/file/htmlcov/index.html?job=python+coverage+report)
[![frontend coverage report](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/badges/develop/coverage.svg?job=gui+tests&key_text=frontend+coverage&key_width=130)](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/commits/develop)
[![DOI](https://joss.theoj.org/papers/10.21105/joss.05388/status.svg)](https://doi.org/10.21105/joss.05388)

**NOMAD** is a web-based research data management software for materials science.
You find the official project homepage and documentation here [https://nomad-lab.eu](https://nomad-lab.eu).
NOMAD is used to provide an open service for managing and publish research data of the same name.
On-premise installations of NOMAD (Oasis) allow research groups to locally manage data with
customized NOMAD version and with their own compute and storage resources.

## Getting started

Here are some resources that will get you started with NOMAD:

- [Project home page](https://nomad-lab.eu)
- [Documentation](https://docs.nomad-lab.eu/)
- [NOMAD deployment hosted by FAIRmat](https://nomad-lab.eu/prod/v1/gui/search/entries)
- [FAIRmat NFDI consortium developing NOMAD](https://www.fairmat-nfdi.eu/fairmat)

## Contributing

See also the more detailed [contributing guide](https://docs.nomad-lab.eu/howto/develop/contrib.html) in the documentation.

There are two forks of this repository, one on **GitHub** and one on MPCDF's **GitLab**.

NOMAD's [GitHub project](https://github.com/FAIRmat-NFDI/nomad) always contains the current `develop` branch.
It can be used to report issues, fork the project, and to create pull requests. After review, pull requests
will be pushed to the GitLab project and merged there. Use the regular GitHub flow to contribute
as an external developer here.

NOMAD's [GitLab project](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR) at [MPCDF](https://www.mpcdf.mpg.de/)
is used for the main development activities. It runs all CI/CD pipelines and official deployments.
It is openly readable, but requires an MPCDF account for active contributions. If you
are a member of the FAIRmat or NOMAD CoE project, contribute here.

Most sub-modules, e.g. NOMAD's parsers, are hosted in individual projects on GitHub within the [FAIRmat-NFDI organization](https://github.com/FAIRmat-NFDI).

## Getting started

For a general project overview visit the official project page [https://nomad-lab.eu](https://nomad-lab.eu). For specific use of the NOMAD software follow these links to our documentation:

- [get started as a developer](https://docs.nomad-lab.eu/howto/develop/setup.html)
- [install and use NOMAD as Python package (to use our APIs or parsers)](https://docs.nomad-lab.eu/howto/oasis/install.html#how-to-install-the-nomad-python-library)
- [install NOMAD Oasis](https://docs.nomad-lab.eu/howto/oasis/install.html)

## Citing NOMAD

If you use this software in your research, for data sharing, or in your lab, we encourage you to cite the following [paper](https://doi.org/10.21105/joss.05388).

```
Scheidgen et al., (2023). NOMAD: A distributed web-based platform for managing materials science research data. Journal of Open Source Software, 8(90), 5388, https://doi.org/10.21105/joss.05388
```

For citation in academic works, you can use this [BibTeX file](docs/assets/joss_paper.bib).

## Changelog

See [`CHANGELOG.md`](./CHANGELOG.md) for more detailed information about changes and fixes.
