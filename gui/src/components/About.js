/*
 * Copyright The NOMAD Authors.
 *
 * This file is part of NOMAD. See https://nomad-lab.eu for further info.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import React, { useLayoutEffect, useRef, useCallback, useEffect, useState, useMemo } from 'react'
import { ReactComponent as AboutSvg } from '../images/about.svg'
import PropTypes from 'prop-types'
import Markdown from './Markdown'
import { isNil } from 'lodash'
import { docsBase, debug, encyclopediaBase, parserMetadata, description, toolkitMetadata as tutorials, footerLinks } from '../config'
import {
  Button,
  Card,
  CardContent,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Grid,
  Link,
  makeStyles,
  Typography,
  Container
} from '@material-ui/core'
import { Link as RouterLink, useHistory } from 'react-router-dom'
import InputConfig from './search/input/InputConfig'
import { useInfo } from './api'
import { pluralize } from '../utils'

const useStylesFooter = makeStyles(theme => ({
  root: {
    backgroundColor: theme.palette.grey[200],
    marginTop: theme.spacing(2),
    flexShrink: 0
  },
  footerContainer: {
    paddingTop: theme.spacing(3),
    paddingBottom: theme.spacing(3),
    textAlign: 'center'
  },
  footerLink: {
    marginLeft: theme.spacing(1),
    marginRight: theme.spacing(1)
  }
}))

function Footer() {
  const classes = useStylesFooter()
  if (!footerLinks || footerLinks.length === 0) {
    return null
  }
  return (
    <footer className={classes.root}>
      <Container className={classes.footerContainer}>
        {footerLinks.map(link => (
          <Link
            key={link.url}
            className={classes.footerLink}
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {link.title}
          </Link>
        ))}
      </Container>
    </footer>
  )
}

/**
 * Displays an info dialog.
 */
function InfoDialog({title, data, DialogProps, onClose}) {
  const handleClose = useCallback(() => onClose?.(), [onClose])

  if (!data) return null

  return <Dialog maxWidth='md' fullWidth open={true} {...DialogProps} onClose={handleClose}>
    <DialogTitle>{title}</DialogTitle>
    <DialogContent>
      <InputConfig data={data} format="YAML" readOnly/>
    </DialogContent>
    <DialogActions>
      <Button onClick={handleClose} color="primary">
        close
      </Button>
    </DialogActions>
  </Dialog>
}
InfoDialog.propTypes = {
  title: PropTypes.string,
  data: PropTypes.object,
  DialogProps: PropTypes.object,
  onClose: PropTypes.func
}

/**
 * Displays a list of information about the version, plugin package and plugin
 * entry points.
 */
export const DistributionInfo = React.memo(({data}) => {
  const [selected, setSelected] = useState()
  const [title, setTitle] = useState()

  const categories = useMemo(() => {
    if (!data) return {}

    const categories = {}
    data.plugin_entry_points
      .forEach((entryPoint) => {
        let category = entryPoint.entry_point_type || entryPoint.plugin_type
        // TODO: Schema plugins are recategorized as package plugins. This can be
        // removed once all plugins have been migrated and we remove the old plugin
        // models.
        if (category === 'schema') {
          category = 'schema_package'
        }
        category = category.replace('_', ' ')
        if (categories[category]) {
          categories[category].push(entryPoint)
        } else {
          categories[category] = [entryPoint]
        }
      })

    Object.keys(categories).forEach(category => {
      categories[category] = categories[category].sort((a, b) => {
        const nameA = a.name
        const nameB = b.name
        return (nameA > nameB) ? 1 : -1
      })
    })

    return categories
  }, [data])

  return data
    ? <ul>
        <li>version: {data.version}</li>
        {data?.plugin_packages?.length
          ? <li>{"plugin packages: "}
            {data.plugin_packages.map((pluginPackage, i) => <>
              <Link key={pluginPackage.name} href="#" onClick={() => {
                setSelected(pluginPackage)
                setTitle('Plugin package')
              }}>{pluginPackage.name}</Link>
              {i !== data.plugin_packages.length - 1 ? ", " : null}
            </>)}
          </li>
          : null
        }
      {Object.keys(categories)
        .sort()
        .map((category) => {
          const entryPoints = categories[category]
          return <li key={category}>
            {`${pluralize(category, 2)}: `}
            {entryPoints
            .sort((a, b) => (a.id > b.id) ? 1 : ((b.id > a.id) ? -1 : 0))
            .map((entryPoint, i) => <>
              <Link key={entryPoint.id} href="#" onClick={() => {
                setSelected(entryPoint)
                setTitle('Plugin entry point')
              }}>{entryPoint.id}</Link>
              {i !== entryPoints.length - 1 ? ", " : null}
            </>)}
          </li>
      })}
      <InfoDialog title={title} data={selected} onClose={() => setSelected(null)} />
    </ul>
    : 'Loading...'
})

DistributionInfo.propTypes = {
  data: PropTypes.object
}

function CodeInfo({code, ...props}) {
  const handleClose = useCallback(() => props.onClose?.(), [props])

  if (!code) {
    return null
  }

  const metadata = parserMetadata[code]

  let introduction = `
    For [${metadata.codeLabel || code}](${metadata.codeUrl}) please provide
    all input and output files and any other relevant files you may have produced.
  `
  if (metadata.tableOfFiles && metadata.tableOfFiles !== '') {
    introduction = `
      For [${metadata.codeLabel || code}](${metadata.codeUrl}) please provide at least
      the files from the following table (if applicable). We recommend to upload
      all input and output files and any other relevant files you may have produced.
      `
  }

  return <Dialog open={true} {...props} onClose={handleClose}>
    <DialogTitle>{metadata.codeLabel || code}</DialogTitle>
    <DialogContent>
      <Markdown>{`
        ${introduction} All files located in the same directory as a *mainfile* (i.e. a parseable
        file which defines an entry) are considered to be associated with the entry.
        You should therefore put all files related to the same entry in the same directory.
        However, try to avoid putting multiple *mainfiles* in the same directory, to avoid
        confusion. For CMS calculations, we recommend a separate directory for each code run.

        ${metadata.tableOfFiles}

        ${(metadata.parserSpecific && metadata.parserSpecific !== '' &&
        `Please note specifically for ${metadata.codeLabel || code}: ${metadata.parserSpecific}`) || ''}

        You can find further information on [the project page for NOMAD's ${metadata.codeLabel || code} parser](${metadata.parserGitUrl}).
      `}</Markdown>
    </DialogContent>
    <DialogActions>
      <Button onClick={handleClose} color="primary">
        close
      </Button>
    </DialogActions>
  </Dialog>
}
CodeInfo.propTypes = {
  code: PropTypes.string,
  onClose: PropTypes.func
}

export const CodeList = React.memo(({withUploadInstructions}) => {
  const [selected, setSelected] = useState(null)

  // Create lists containing code name and category
  const categorySizes = {}
  const codes = Object.entries(parserMetadata)
    .filter(([code, metadata]) => metadata && code !== 'example')
    .map(([code, metadata], index) => {
      const name = metadata.codeLabel || code
      const category = metadata.codeCategory
      if (categorySizes[category]) {
        categorySizes[category] += 1
      } else {
        categorySizes[category] = 1
      }

      if (withUploadInstructions) {
        return [code, category, <Link key={index} href="#" onClick={() => setSelected(code)}>{name}</Link>]
      } else if (metadata.codeUrl) {
        return [code, category, <Link key={index} href={metadata.codeUrl} target="code">{name}</Link>]
      } else {
        return [code, category, name]
      }
    })

  // Sort by category size, then by program name. Codes without category go to
  // the end.
  codes.sort((a, b) => {
    const nameA = a[0]
    const nameB = b[0]
    const categoryA = a[1]
    const categoryB = b[1]
    const sizeA = categorySizes[categoryA]
    const sizeB = categorySizes[categoryB]
    if (isNil(categoryA) && !isNil(categoryB)) return 1
    if (isNil(categoryB) && !isNil(categoryA)) return -1
    if (sizeA > sizeB) return -1
    if (sizeA < sizeB) return 1
    if (nameA > nameB) return 1
    if (nameA < nameB) return -1
    return 0
  })

  // Create a renderable version
  let currentCategory = null
  let categoryIndex = 0
  const codeshtml = codes.reduce((list, value) => {
    let index = -1
    const categoryTmp = value[1]
    const html = value[2]
    const category = categoryTmp ? pluralize(categoryTmp) : 'Miscellaneous'

    if (currentCategory !== category) {
      index = 0
      if (categoryIndex !== 0) {
        list.push(', ')
      }
      list.push(<b> {category}: </b>)
      categoryIndex += 1
      currentCategory = category
    }

    if (html) {
      if (index !== 0) {
        list.push(', ')
      }
      list.push(html)
    }
    return list
  }, [])

  return <span data-testid="code-list">
    {codeshtml.map((html, index) => <React.Fragment key={`codeListFragment${index}`}>{html}</React.Fragment>)}
    <CodeInfo code={selected} onClose={() => setSelected(null)} />
  </span>
})
CodeList.propTypes = {
  withUploadInstructions: PropTypes.bool
}

const useCardStyles = makeStyles(theme => ({
  title: {
    marginBottom: theme.spacing(1)
  }
}))

function InfoCard({title, children, xs, top, bottom}) {
  const classes = useCardStyles()
  const style = {}
  if (top) {
    style['paddingBottom'] = 0
  }
  if (bottom) {
    style['paddingTop'] = 0
  }
  return <Grid item xs={xs} style={style}>
    <Card>
      <CardContent>
        <Typography variant="h6" className={classes.title}>{title}</Typography>
        <Typography component="div">{children}</Typography>
      </CardContent>
    </Card>
  </Grid>
}
InfoCard.propTypes = {
  title: PropTypes.string.isRequired,
  children: PropTypes.node,
  xs: PropTypes.number,
  top: PropTypes.bool,
  bottom: PropTypes.bool
}

const useStyles = makeStyles(theme => ({
  root: {
    height: "100dvh",
    display: 'flex',
    flexDirection: 'column'
  },
  mainContent: {
    flex: '1 0 auto',
    padding: theme.spacing(3)
  },
  container: {
    padding: theme.spacing(3),
    boxSizing: 'content-box',
    maxWidth: 1024,
    margin: 'auto',
    width: '100%'
  },
  header: {
    margin: '24px 0px'
  }
}))

export default function About() {
  const classes = useStyles()
  const info = useInfo()
  const svg = useRef()
  const history = useHistory()

  const makeClickable = useCallback((id, onClick) => {
    const element = svg.current.querySelector('#' + id)
    element.style.cursor = 'pointer'
    element.firstChild.onclick = () => {
      onClick()
    }
  }, [svg])

  const setText = useCallback((id, lines) => {
    const element = svg.current.querySelector('#' + id)
    const x = element.getAttribute('x')
    element.innerHTML = lines.map((line, i) => `<tspan x="${x}" dy="${i === 0 ? '0' : '1.2em'}">${line}</tspan>`).join('')
  }, [svg])

  useLayoutEffect(() => {
    makeClickable('upload', () => {
      history.push('/upload')
    })
    if (encyclopediaBase) {
      makeClickable('encyclopedia', () => {
        window.location.href = encyclopediaBase
      })
    }
    makeClickable('search', () => {
      history.push('/search')
    })
  }, [svg, makeClickable, setText, history])

  useEffect(() => {
    const statistics = (info && {...info.statistics}) || {}
    statistics.n_tutorials = tutorials?.tutorials?.length || 25
    const value = (key, unit) => {
      const nominal = statistics[key]
      let stringValue = null
      if (nominal) {
        if (nominal >= 1.0e+9) {
          stringValue = (nominal / 1.0e+9).toFixed(1) + ' billion'
        } else if (nominal >= 1.0e+6) {
          stringValue = (nominal / 1.0e+6).toFixed(1) + ' million'
        } else if (nominal >= 1.0e+3) {
          stringValue = (nominal / 1.0e+3).toFixed(1) + ' thousand'
        } else {
          stringValue = nominal.toString()
        }
        return `${stringValue || '...'} ${unit}`
      } else {
        return '...'
      }
    }
    setText('repositoryStats', [
      value('n_entries', 'entries')
      // value('n_uploads', 'uploads')
    ])
    setText('archiveStats', [
      value('n_calculations', 'results'),
      value('n_quantities', 'quantities')
    ])
    setText('encStats', [
      value('n_materials', 'materials')
    ])
    setText('toolkitStats', [
      `${statistics.n_tutorials} notebooks`
    ])
  }, [svg, info, setText])

  return <div className={classes.root}>
    <Grid className={classes.container} container spacing={2}>
      <Grid item xs={12}>
        <Markdown>{`
        # **NOMAD** &ndash; Manage and Publish Materials Data

        ${description}

        You can learn more about NOMAD on its [homepage](https://nomad-lab.eu/nomad-lab/)
        or our [documentation](${docsBase}/index.html).
        `}</Markdown>
      </Grid>
      <InfoCard xs={6} title="Interactive Search" top>
        NOMAD extracts <b>rich metadata</b> from uploaded raw-data. <Link component={RouterLink} to={'/search'}>
        Explore NOMAD&apos;s data</Link> by creating complex queries from interactive data visualizations of key
        properties, including the simulated composition/system, used method, upload metadata,
        as well as material classifications and available quantities. Or use
        the <b>Optimade</b> filter language to add arbitrarily nested queries.
      </InfoCard>
      <InfoCard xs={6} title="A common data format" top>
        NOMAD provides data in <i>processed</i> and <i>normalized</i> form in a machine processable and common hierarchical format.
        This <i>processed data</i>, i.e. the <b>NOMAD Archive</b>, is organized into nested sections of quantities with well defined units,
        data types, shapes, and descriptions. These definitions are called the <b>NOMAD Metainfo</b> and they
        can be <Link component={RouterLink} to={'/analyze/metainfo'}>browsed here</Link>.
      </InfoCard>
      <Grid item xs={12} style={{paddingTop: 0, paddingBottom: 0}}>
        <AboutSvg ref={svg}></AboutSvg>
      </Grid>
      <InfoCard xs={4} title="Uploading is simple" bottom>
        <p>
        You provide your own data <i>as is</i>. Just zip your files as they are,
        including nested directory structures and potential auxiliary files, and upload
        up to 32GB in a single .zip or .tar(.gz) file. NOMAD will automatically discover
        and process the relevant files.
        </p>
        <p>
        You can <b>privately</b> inspect, curate, or delete your data before publishing.
        Data can be published with an <b>embargo (up to 3 years)</b> to only share data with
        selected users.
        </p>
        <p>
        Add additional metadata like <b>comments</b>, <b>references</b> to websites or papers, and
        your <b>co-authors</b>. Organize the uploaded entries into <b>datasets</b> and
        cite your data with a <b>DOI</b> that we provide on request.
        </p>
        <p>
          You can provide via GUI or shell command or manage already uploaded
          data <Link component={RouterLink} to={'/user'}>here</Link>.
        </p>
      </InfoCard>
      <InfoCard xs={4} title="Processing" bottom>
        <p>
        Uploaded data is automatically processed and made available
        in the uploaded <b>raw files</b> or in its unified <b>processed data</b> form.
        NOMAD parsers convert raw files into NOMAD&apos;s common data format.
        You can inspect the processed data and extracted metadata before
        publishing your data.
        </p>
        <p>NOMAD supports most community codes and file formats: <CodeList/></p>
        <p>
        To use NOMAD&apos;s parsers and normalizers outside of NOMAD read <Link href={`${docsBase}/howto/plugins/types/parsers.html#running-a-parser`}>here</Link>.
        Read <Link href={`${docsBase}/howto/oasis/install.html#how-to-install-the-nomad-python-library`}>here</Link> on how to install
        our software and how to use NOMAD processing in your Python environment.
        </p>
      </InfoCard>
      <InfoCard xs={4} title="APIs" bottom><Markdown>{`
      NOMAD can also be accessed programmatically via REST APIs.
      There is the proprietary NOMAD API, an implementation of the
      standardized [OPTiMaDe API](https://github.com/Materials-Consortia/OPTiMaDe/tree/master)
      materials science database API, and more.

      We offer a [how-to guide on using the API with plain Python](${docsBase}/howto/manage/program/api.html).
      Another [how-to guide covers the installation and use of NOMAD's API client library](${docsBase}/howto/manage/program/archive_query.html).
      The [NOMAD Analytics Toolkit](https://nomad-lab.eu/AIToolkit) allows to use
      this without installation and directly on NOMAD servers.

      Visit our [API page](/analyze/apis).
      `}</Markdown></InfoCard>
      <Grid item xs={12}>
        <Markdown>{`
        ### Getting Help
        If you encounter any difficulties, please write to
        support@nomad-lab.eu. If you think
        that this web-page is not working as expected, or if you want to start a discussion
        about possible features, feel free to open an issue on our
        [GitHub project](https://github.com/FAIRmat-NFDI/nomad/issues).

        ### Developer Documentation
        The [in-depth documentation](${docsBase}/index.html)
        contains a general introduction to NOMAD and its underlying architecture,
        more information and tutorials, how to prepare uploads, how
        to use the API, developer information, how to operate your own NOMAD (a so called
        Oasis), how to contribute parsers, and much more.

        ### Source code
        The source-code for NOMAD is maintained
        at the MPCDF's [GitLab](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR).
        To push code, you need an MPCDF account and you can apply
        [here](https://www.mpcdf.mpg.de/userspace/new-users).

        ${debug ? `
        ### Material science data and domains
        Originally NOMAD was build for DFT calculations and data from the respective
        community codes. But NOMAD is now extended to support multiple materials science domains,
        such as experiments, synthesis, and computational methods at different scales.` : ''}

        ${debug ? `
        ### Log management with Elastic stack
        We use a central logging system based on the *elastic*-stack
        (previously called *Elastic Logstash Kibana* (ELK)-stack).
        This system pushes logs, events, monitoring data,
        and other application metrics to a central database where it
        can be analysed visually by us.

        ### Test user
        During development this GUI might not be connected to the actual NOMAD
        repository. Therefore, you cannot create a user or login with an existing
        user. You might use the test user \`leonard.hofstadter@nomad-fairdi.tests.de\`
        with password \`password\`. The user \`sheldon.cooper@nomad-fairdi.tests.de\` is
        used for data that has no provenance with the original NOMAD CoE database.
        ` : ''}
        `}</Markdown>
      </Grid>
      <Grid item xs={12}>
        <Typography variant='h5' className={classes.header}>About this distribution</Typography>
        <DistributionInfo data={info} />
      </Grid>
    </Grid>
    <Footer />
  </div>
}
