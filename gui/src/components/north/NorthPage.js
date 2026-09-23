/* eslint-disable quotes */
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
import React, { useCallback, useState } from 'react'
import {
  Grid,
  makeStyles,
  Box,
  Accordion,
  Typography,
  AccordionSummary,
  AccordionDetails,
  Link,
  Chip
} from '@material-ui/core'
import ExpandMoreIcon from '@material-ui/icons/ExpandMore'
import Page from '../Page'
import { withLoginRequired } from '../api'
import Markdown from '../Markdown'
import DefaultIcon from '@material-ui/icons/Assessment'
import Icon from '@material-ui/core/Icon'
import NorthTool, { getIconUrl, NorthToolButtons, useNorthTool } from './NorthTool'
import { northEnabled, northTools as _tools } from '../../config'

export const help = `
The NOMAD Remote Tools Hub (NORTH) provides access to tools which you can use to
work remotely on unpublished data stored within NOMAD.
`

const useNorthToolStyles = makeStyles(theme => ({
  description: {
    '& > :first-child': {
      marginTop: '0 !important'
    }
  },
  iconImg: {
    height: '100%'
  },
  screenShotImg: {
    width: '100%'
  }
}))

const NorthToolAccordion = React.memo(function NorthToolAccordion({...props}) {
  const {
    state,
    title,
    icon,
    file_extensions,
    maintainer,
    short_description,
    description} = useNorthTool()
  const classes = useNorthToolStyles()

  return (
    <Accordion {...props}>
      <AccordionSummary
        expandIcon={<ExpandMoreIcon />}
      >
        <Grid container direction="row" spacing={2}>
          <Grid item>
            {icon ? (
              <Icon>
                <img
                  className={classes.iconImg}
                  src={getIconUrl(icon)}
                  alt="icon"
                />
              </Icon>
            ) : (
              <DefaultIcon />
            )}
          </Grid>
          <Grid item xs={2}>
            <Typography><b>{title}</b></Typography>
          </Grid>
          {short_description && (
            <Grid item >
              <Typography>
                {short_description}
              </Typography>
            </Grid>
          )}
          <Grid item>
            {state === 'running' && (
              <Chip size="small" color="primary" label="running" />
            )}
          </Grid>
        </Grid>
      </AccordionSummary>
      <AccordionDetails>
        <Grid container spacing={2}>
          <Grid item xs={8}>
            <Typography>
              <Markdown className={classes.description}>
                {description || '*Missing tool description*'}
              </Markdown>
            </Typography>
          </Grid>
          <Grid item xs={4}>
            <Box display="flex" flexDirection="column">
              {maintainer && (
                <Typography>
                  <b>Maintainer: </b>{maintainer
                    .map((maintainer, index) => (
                      <Link
                        key={index}
                        href={`mailto:${maintainer.email}`}
                      >
                        {maintainer.name}
                      </Link>
                    )).reduce((prev, curr) => [prev, ', ', curr])
                  }
                </Typography>
              )}
              {file_extensions && file_extensions.length > 0 && (
                <Typography>
                  <b>File extensions: </b>{file_extensions
                    .map((extension, index) => <span key={index}>{extension}</span>)
                    .reduce((prev, curr) => [prev, ', ', curr])
                  }
                </Typography>
              )}
              <Box flexGrow={1}>&nbsp;</Box>
              <NorthToolButtons/>
            </Box>
          </Grid>
        </Grid>
      </AccordionDetails>
    </Accordion>
  )
})
NorthToolAccordion.propTypes = {}

/**
 * Landing page for the NOMAD Remote Tools Hub.
 */
const NorthPage = React.memo(() => {
  const tools = useTools()
  const [expanded, setExpanded] = useState('')

  const handleChange = useCallback(tool => {
    setExpanded(value => value === tool.name ? null : tool.name)
  }, [setExpanded])

  return (
    <Page limitedWidth>
      <Box marginTop={3}>
        <Typography variant="h4" color="primary">
          Welcome to <b>NORTH</b> (NOMAD Remote Tools Hub)
        </Typography>
      </Box>
      <Box marginY={1}>
        <Typography>
          NORTH allows you to run pre-configured applications directly on the server. With
          NORTH you can access, analyze, and create data directly in your uploads, using
          your favorite tools, no setup required.
        </Typography>
      </Box>
      <Box marginY={1}>
        <Typography>
          This page shows all available tools. It also show which tools are already running
          for you. For each tool each user can only run one instance at a time. Some
          tools can also be launched from the file browser in your uploads, depending on
          file types.
        </Typography>
      </Box>
      <Box marginY={2}>
        {Object
          .keys(tools)
          .map(key => ({
            name: tools[key].id_url_safe,
            title: tools[key].north_tool.display_name ?? key,
            ...tools[key].north_tool
          }))
          .sort((a, b) => a.title.localeCompare(b.title))
          .map((tool, index) => (
            <NorthTool key={index} tool={tool}>
              <NorthToolAccordion
                expanded={tool.name === expanded}
                onChange={() => handleChange(tool)}
              />
            </NorthTool>
          ))
        }
      </Box>
    </Page>
  )
})

export default withLoginRequired(NorthPage)

/**
 * Hook for loading the list of available tools from the NORTH API.
*/
export function useTools() {
  if (!northEnabled) {
    return {}
  }
  return _tools
}
