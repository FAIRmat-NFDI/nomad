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
import React, { useState } from 'react'
import PropTypes from 'prop-types'
import { apiBase } from '../../config'
import { refreshBrowserAuth } from '../api'
import { useKeycloak } from '@react-keycloak/web'
import { Tooltip, IconButton, Menu, MenuItem } from '@material-ui/core'
import DownloadIcon from '@material-ui/icons/CloudDownload'
import { useErrors } from '../errors'

const EntryDownloadButton = React.memo(function EntryDownloadButton(props) {
  const {tooltip, disabled, buttonProps, dark, query} = props
  const { keycloak } = useKeycloak()
  const { raiseError } = useErrors()
  const [anchorEl, setAnchorEl] = useState(null)

  const handleClick = event => {
    event.stopPropagation()
    setAnchorEl(event.currentTarget)
  }

  const handleSelect = async (urlSuffix) => {
    setAnchorEl(null)
    const queryStringData = query
    const owner = query.visibility || 'visible'
    const url = `${apiBase}/v1/entries/${urlSuffix}?owner=${owner}&json_query=${JSON.stringify(queryStringData)}`
    try {
      await refreshBrowserAuth(keycloak)
      window.location.assign(url)
    } catch (error) {
      raiseError(new Error('Could not refresh your session before downloading. Please sign in again and retry.'))
    }
  }

  const handleClose = () => {
    setAnchorEl(null)
  }

  return <React.Fragment>
    <IconButton
      {...buttonProps}
      disabled={disabled}
      onClick={handleClick}
      style={dark ? {color: 'white'} : null}
    >
      <Tooltip title={tooltip || 'Download'}>
        <DownloadIcon />
      </Tooltip>
    </IconButton>
    <Menu
      anchorEl={anchorEl}
      open={Boolean(anchorEl)}
      onClose={handleClose}
    >
      <MenuItem onClick={() => handleSelect('raw')}>Raw uploaded files</MenuItem>
      <MenuItem onClick={() => handleSelect('archive/download')}>Processed data</MenuItem>
    </Menu>
  </React.Fragment>
})
EntryDownloadButton.propTypes = {
  /**
   * The query that defines what to download.
   */
  query: PropTypes.object.isRequired,
  /**
   * A tooltip for the button
   */
  tooltip: PropTypes.string,
  /**
   * Whether the button is disabled
   */
  disabled: PropTypes.bool,
  /**
   * Properties forwarded to the button.
   */
  buttonProps: PropTypes.object,
  dark: PropTypes.bool
}

export default EntryDownloadButton
