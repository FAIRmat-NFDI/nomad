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

import React from 'react'
import PropTypes from 'prop-types'
import H5Web from '../../visualization/H5Web'
import { Card, makeStyles } from '@material-ui/core'

const useNexusCardStyles = makeStyles(theme => ({
  root: {
    height: 500
  }
}))

const VALID_NEXUS_PARSERS = [
  'pynxtools.nomad.entrypoints:nexus_parser',
  'pynxtools.nomad.parsers:nexus_parser',
  'pynxtools.nomad.parsers:nexus_parser_v2'
]

const NexusCard = React.memo(function NexusCard({index}) {
  const classes = useNexusCardStyles()
  if (!VALID_NEXUS_PARSERS.includes(index.parser_name)) {
    return null
  }
  return (
    <Card className={classes.root}>
      <H5Web upload_id={index.upload_id} filename={index.mainfile} initialPath="/"/>
    </Card>
  )
})
NexusCard.propTypes = {
  index: PropTypes.object.isRequired
}

export default NexusCard
