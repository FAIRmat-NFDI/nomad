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
import { apiBase } from '../../config'
import { refreshBrowserAuth } from '../api'
import { useKeycloak } from '@react-keycloak/web'
import { makeStyles, Tooltip } from '@material-ui/core'
import { useErrors } from '../errors'

const useStyles = makeStyles(theme => ({
  root: {}
}))

export const Download = React.memo(function Download(props) {
  const classes = useStyles(props)
  const { keycloak } = useKeycloak()
  const { raiseError } = useErrors()
  const {url, component, children, disabled, color, size, tooltip} = props

  const handleClick = async () => {
    try {
      await refreshBrowserAuth(keycloak)
      window.location.assign(`${apiBase}/v1/${url}`)
    } catch (error) {
      raiseError(new Error('Could not refresh your session before downloading. Please sign in again and retry.'))
    }
  }

  const Component = component

  const button = (
    <Component
      className={classes.root}
      disabled={disabled} color={color} size={size}
      onClick={handleClick}
      data-testid={props['data-testid']}
    >
      {children}
    </Component>
  )

  if (tooltip && !disabled) {
    return <Tooltip title={tooltip}>{button}</Tooltip>
  } else {
    return button
  }
})
Download.propTypes = {
  url: PropTypes.string,
  component: PropTypes.any,
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ]).isRequired,
  disabled: PropTypes.bool,
  tooltip: PropTypes.string,
  color: PropTypes.string,
  size: PropTypes.string,
  'data-testid': PropTypes.string
}

export default Download
