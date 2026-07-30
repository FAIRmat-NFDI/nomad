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
import React, { useEffect, useMemo, useState, useContext } from 'react'
import {
  atom,
  useSetRecoilState,
  useRecoilValue,
  useRecoilState
} from 'recoil'
import PropTypes from 'prop-types'
import Cookies from 'universal-cookie'
import { apiBase, globalLoginRequired, searchQuantities } from '../config'
import { Box, Typography, withStyles } from '@material-ui/core'
import LoginLogout from './LoginLogout'
import { useKeycloak } from '@react-keycloak/web'
import axios from 'axios'
import { useErrors } from './errors'

export class DoesNotExist extends Error {
  constructor(msg) {
    super(msg)
    this.name = 'DoesNotExist'
  }
}

export class NotAuthorized extends Error {
  constructor(msg) {
    super(msg)
    this.name = 'NotAuthorized'
  }
}

export class ApiError extends Error {
  constructor(msg) {
    super(msg)
    this.name = 'CannotReachApi'
  }
}

export class ApiRequestError extends Error {
  constructor(msg) {
    super(msg)
    this.name = 'BadRequest'
  }
}

function handleApiError(e) {
  if (e.name === 'CannotReachApi' || e.name === 'NotAuthorized' || e.name === 'DoesNotExist' || e.name === 'BadRequest') {
    throw e
  }

  let error = null
  if (e?.message === 'Failed to fetch' || e?.message === 'Network Error') {
    error = new ApiError(e.message)
    error.status = 400
  } else if (e.response) {
    const body = e.response.body
    const message = (body && (body.message || body.description)) || e.response?.data?.detail || e.response.statusText
    const errorMessage = `${message} (${e.response.status})`
    if (e.response.status === 404) {
      error = new DoesNotExist(errorMessage)
    } else if (e.response.status === 401) {
      error = new NotAuthorized(errorMessage)
    } else if (e.response.status === 400) {
      error = new ApiRequestError(errorMessage)
    } else if (e.response.status === 502) {
      error = new ApiError(errorMessage)
    } else {
      error = new Error(errorMessage)
    }
    error.status = e.response.status
    error.apiMessage = message
  } else {
    const errorMessage = e.status ? `${e} (${e.status})` : '' + e
    error = new Error(errorMessage)
  }
  throw error
}

class Api {
  constructor(keycloak, setLoading) {
    this.keycloak = keycloak
    this.setLoading = setLoading
    this.baseURL = `${apiBase}/v1`
    this.axios = axios.create({
      baseURL: this.baseURL,
      paramsSerializer: {indexes: null}
    })

    this.nLoading = 0

    this.onFinishLoading = (show) => {
      if (show) {
        this.nLoading--
        if (this.nLoading === 0) {
          setLoading(false)
        }
      }
    }
    this.onStartLoading = (show) => {
      if (show) {
        this.nLoading++
        if (this.nLoading > 0) {
          setLoading(true)
        }
      }
    }

    this.subscriptions = {}
  }

  /**
   * Fetches the up-to-date authorization headers. Performs a token update if
   * the current token has expired.
   * @returns Object containing the authorization header.
   */
  async authHeaders() {
    if (this.keycloak.token) {
      return new Promise((resolve, reject) => {
        this.keycloak.updateToken()
          .then(() => {
            resolve({
              headers: {
                'Authorization': `Bearer ${this.keycloak.token}`
              }
            })
          })
          .catch(() => {
            reject(new ApiError())
          })
      })
    } else {
      return {}
    }
  }

  /**
   * Returns the data related to the specified dataset.
   *
   * @param {string} datasetID
   * @returns Object containing the search index contents.
   */
  async datasets(datasetId) {
    this.onStartLoading()
    const auth = await this.authHeaders()
    try {
      const entry = await this.axios.get(`/datasets/${datasetId}`, auth)
      return entry.data.data
    } catch (errors) {
      handleApiError(errors)
    } finally {
      this.onFinishLoading()
    }
  }

  /**
   * Returns a list of suggestions for the given metainfo quantities.
   *
   * @param {string} quantity The quantity names for which suggestions are
   * returned.
   * @param {string} input Input used to filter the responses. Must be provided
   * in order to return suggestions.
   *
   * @returns List of suggested values. The items are ordered by how well they
   * match the input.
   */
  async suggestions(quantities, input) {
    const auth = await this.authHeaders()
    try {
      const suggestions = await this.axios.post(
        `/suggestions`,
        {
          input: input,
          quantities: quantities
        },
        auth
      )
      return suggestions.data
    } catch (errors) {
      handleApiError(errors)
    }
  }

  /**
   * Return the raw file metadata for a given entry.
   * @param {string} entryId
   * @returns Object containing the raw file metadata.
   */
  async getRawFileListFromEntry(entryId) {
    this.onStartLoading()
    const auth = await this.authHeaders()
    try {
      const entry = await this.axios.get(`/entries/${entryId}/rawdir`, auth)
      return entry.data
    } catch (errors) {
      handleApiError(errors)
    } finally {
      this.onFinishLoading()
    }
  }

  /**
   * Executes the given entry query
   * @param {object} search contains the search object
   * @param {string} searchTarget The target of the search: entries or materials
   * @returns Object containing the raw file metadata.
   */
  async query(searchTarget, search, config) {
    this.onStartLoading(config?.loadingIndicator || false)
    const auth = await this.authHeaders()
    try {
      const request = {
        method: 'POST',
        path: `${searchTarget}/query`,
        url: `${this.baseURL}/${searchTarget}/query`,
        body: search
      }
      const result = await this.axios.post(request.path, request.body, auth)
      if (config.returnRequest) {
        request.response = result.data
        return request
      } else {
        return result.data
      }
    } catch (errors) {
      handleApiError(errors)
    } finally {
      this.onFinishLoading(config?.loadingIndicator || false)
    }
  }

  async get(path, query, config) {
    const GET = (path, body, config) => this.axios.get(path, config)
    return this.doHttpRequest(GET, path, null, {params: query, methodName: 'GET', ...config})
  }

  async post(path, body, config) {
    const POST = (path, body, config) => this.axios.post(path, body, config)
    return this.doHttpRequest(POST, path, body, {methodName: 'POST', ...config})
  }

  async put(path, body, config) {
    const PUT = (path, body, config) => this.axios.put(path, body, config)
    return this.doHttpRequest(PUT, path, body, {methodName: 'PUT', ...config})
  }

  async delete(path, config) {
    const DELETE = (path, body, config) => this.axios.delete(path, config)
    return this.doHttpRequest(DELETE, path, null, {methodName: 'DELETE', ...config})
  }

  async doHttpRequest(method, path, body, config) {
    const noLoading = config?.noLoading
    this.onStartLoading(!noLoading)
    const auth = await this.authHeaders()
    config = config || {}
    config.params = config.params || {}
    config.headers = {
      accept: 'application/json',
      ...auth.headers,
      ...(config?.headers || {})
    }
    try {
      const results = await method(path, body, config)
      if (config.jsonResponse) {
        results.data = repairJsonResponse(results.data)
      }
      const response = config.fullResponse
        ? results
        : results.data
      if (config.returnRequest) {
        return {
          method: config.methodName || method.name,
          url: `${this.baseURL}/${path}`,
          body: body,
          response: response
        }
      }
      return response
    } catch (errors) {
      if (config.noHandleErrors) {
        throw errors
      }
      handleApiError(errors)
    } finally {
      this.onFinishLoading(!noLoading)
    }
  }

  async getGroups(query) {
    // no loading indicator, because this is only used in the background of the edit dialog
    return this.get('groups', query, {noLoading: true}).then(response => response.data)
  }

  async getUsers(query) {
    // no loading indicator, because this is only used in the background of the edit dialog
    return this.get('users', query, {noLoading: true}).then(response => response.data)
  }

  async inviteUser(user) {
    return this.put('users/invite', user, {noHandleErrors: true})
  }

  async getDatasets(prefix) {
    // no loading indicator, because this is only used in the background of the edit dialog
    const user = await this.keycloak.loadUserInfo()
    const query = {
      prefix: prefix,
      dataset_type: 'owned',
      user_id: user.id,
      page_size: 1000
    }
    return this.get('datasets', query, {noLoading: true}).then(response => response.data)
  }

  async edit(edit) {
    // We do not call the start and finish loading callbacks, because this one is
    // only used in the background.

    // repair the query, the API will only access correct use of lists for many
    // quantities
    Object.keys(edit.query).forEach(quantity => {
      if (searchQuantities[quantity] && searchQuantities[quantity].many) {
        if (!Array.isArray(edit.query[quantity])) {
          edit.query[quantity] = edit.query[quantity].split(',')
        }
      }
    })
    return this.post('entries/edit_v0', edit)
  }
}

/**
 * Custom JSON parse function that can handle NaNs that can be created by the
 * python JSON serializer.
 */
function repairJsonResponse(result) {
  if (typeof result === 'string') {
    try {
      return JSON.parse(result)
    } catch (e) {
      try {
        return JSON.parse(result.replace(/\bNaN\b/g, '"NaN"'))
      } catch (e) {
        return result
      }
    }
  } else {
    return result
  }
}

/**
 * React context that provides access to the API, user information and server
 * information throughout the app.
 */
export const apiContext = React.createContext()
export const APIProvider = React.memo(({
  children
}) => {
  const {keycloak} = useKeycloak()
  const setLoading = useSetLoading()
  const api = useState(new Api(keycloak, setLoading))[0]
  const [user, setUser] = useState()

  // Update user whenever keycloak instance changes
  useEffect(() => {
    if (!keycloak.authenticated) {
      setUser(null)
      return
    }

    keycloak.loadUserInfo().then(async userInfo => {
      api.getGroups({user_id: userInfo.sub}).then(groups => {
        userInfo.groups = groups.map(group => group.group_id)
        setUser(userInfo)
      }).catch(() => {
        userInfo.groups = []
        setUser(userInfo)
      })
    }).catch(() => {
      setUser(null)
    })
  }, [api, keycloak, setUser])

  const value = useMemo(() => ({
    api: api,
    user: user
  }), [api, user])

  return <apiContext.Provider value={value}>
    {children}
  </apiContext.Provider>
})
APIProvider.propTypes = {
  children: PropTypes.node
}

/**
 * Hook for using server info.
*/
const infoState = atom({
  key: 'info',
  default: undefined
})
export function useInfo() {
  const [info, setInfo] = useRecoilState(infoState)
  const { api } = useApi()

  // Get info only once
  useEffect(() => {
    if (!info) {
      api.get('/info').then(setInfo).catch(() => {})
    }
  }, [info, api, setInfo])

  return info
}

/**
 * Hook for using the API context.
*/
export function useApi() {
  return useContext(apiContext)
}

/**
 * Hooks/state for reading/writing whether the API is loading something.
*/
const apiLoading = atom({
  key: 'apiLoading',
  default: false
})
export function useLoading() {
  return useRecoilValue(apiLoading)
}
export function useLoadingState() {
  return useRecoilState(apiLoading)
}
export function useSetLoading() {
  return useSetRecoilState(apiLoading)
}

function VerifyGlobalLogin({children}) {
  const {api} = useApi()
  const [verified, setVerified] = useState(null)

  useEffect(() => {
    api.get('users/me').then(() => setVerified(true)).catch(() => setVerified(false))
  }, [api, setVerified])

  if (verified === null) {
    return null
  }

  if (!verified) {
    return <Box margin={2}>
      <LoginLogout color="primary" />
      <Typography color="error">
        You are not allowed to access this NOMAD installation. Please logout and try again.
      </Typography>
    </Box>
  }

  return <React.Fragment>
    {children}
  </React.Fragment>
}
VerifyGlobalLogin.propTypes = {
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ]).isRequired
}

export function GlobalLoginRequired({children}) {
  if (!globalLoginRequired) {
    return <React.Fragment>
      {children}
    </React.Fragment>
  }

  return <LoginRequired message="You have to be logged in to use this NOMAD installation.">
    <VerifyGlobalLogin>
      {children}
    </VerifyGlobalLogin>
  </LoginRequired>
}
GlobalLoginRequired.propTypes = {
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ]).isRequired
}

export const LoginRequired = withStyles(theme => ({
  root: {
    padding: theme.spacing(2),
    display: 'flex',
    alignItems: 'center',
    '& p': {
      marginRight: theme.spacing(1)
    }
  }
}))(({classes, message, children}) => {
  const {api} = useApi()
  if (api.keycloak.authenticated) {
    return <React.Fragment>
      {children}
    </React.Fragment>
  } else {
    return <div className={classes.root}>
      <Typography>
        {message || 'You have to login to use this functionality.'}
      </Typography>
      <LoginLogout color="primary" />
    </div>
  }
})
LoginRequired.propTypes = {
  message: PropTypes.string,
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ]).isRequired
}

/**
 * HOC for wrapping components that require a login. Without login will return
 * the given message together with a login link.
 *
 * @param {*} Component
 * @param {*} message The message to display
 */
export function withLoginRequired(Component, message) {
  return ({...props}) => <LoginRequired message={message}>
    <Component {...props} />
  </LoginRequired>
}

export const withApi = (Component) => React.forwardRef((props, ref) => {
  const apiProps = useApi()
  const {raiseError} = useErrors()
  return <Component ref={ref} {...apiProps} raiseError={raiseError} {...props} />
})

const prefixPaths = (path) => {
  const prefixes = ['/']
  let prefix = ''

  for (const segment of path.split('/').filter(Boolean)) {
    prefix += `/${segment}`
    prefixes.push(prefix)
  }

  return prefixes
}

export const syncAuthorizationCookie = (keycloak) => {
  const cookies = new Cookies()
  const path = apiBase.startsWith('/') ? apiBase : new URL(apiBase).pathname
  const ancestorPaths = prefixPaths(path).filter(p => p !== path)
  for (const p of ancestorPaths) cookies.remove('Authorization', {path: p})
  if (keycloak.authenticated) {
    // Authenticated. Set authentication cookie.
    cookies.set('Authorization', 'Bearer ' + keycloak.token,
      {
        path: path,
        expires: new Date((keycloak.tokenParsed.exp + keycloak.timeSkew) * 1000),
        sameSite: 'strict'
      })
  } else {
    // Not authenticated. Remove the current cookie as well.
    cookies.remove('Authorization', {path: path})
  }
}

/**
 * Refreshes the access token and synchronizes the cookie used by browser
 * navigations, which cannot carry the normal Axios Authorization header.
 */
export const refreshBrowserAuth = async (keycloak, minValidity = 30) => {
  if (!keycloak.authenticated) {
    return
  }

  await keycloak.updateToken(minValidity)
  syncAuthorizationCookie(keycloak)
}

export const onKeycloakEvent = (keycloak) => () => syncAuthorizationCookie(keycloak)
