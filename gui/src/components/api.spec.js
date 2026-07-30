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

import Cookies from 'universal-cookie'
import { apiBase } from '../config'
import { refreshBrowserAuth, syncAuthorizationCookie } from './api'

jest.mock('universal-cookie', () => jest.fn())

const currentPath = apiBase.startsWith('/') ? apiBase : new URL(apiBase).pathname

describe('refreshBrowserAuth', () => {
  const set = jest.fn()
  const remove = jest.fn()

  beforeEach(() => {
    Cookies.mockImplementation(() => ({set, remove}))
    jest.clearAllMocks()
  })

  it('refreshes the token and synchronizes the navigation cookie', async () => {
    const keycloak = {
      authenticated: true,
      token: 'old-token',
      tokenParsed: {exp: 100},
      timeSkew: 0,
      updateToken: jest.fn().mockImplementation(async () => {
        keycloak.token = 'fresh-token'
        keycloak.tokenParsed = {exp: 200}
      })
    }

    await refreshBrowserAuth(keycloak)

    expect(keycloak.updateToken).toHaveBeenCalledWith(30)
    expect(set).toHaveBeenCalledWith(
      'Authorization',
      'Bearer fresh-token',
      expect.objectContaining({sameSite: 'strict'})
    )
  })

  it('does not create a cookie when refreshing fails', async () => {
    const keycloak = {
      authenticated: true,
      updateToken: jest.fn().mockRejectedValue(new Error('refresh failed'))
    }

    await expect(refreshBrowserAuth(keycloak)).rejects.toThrow('refresh failed')
    expect(set).not.toHaveBeenCalled()
  })

  it('clears cookies stranded at ancestor deployment paths while authenticated', () => {
    const keycloak = {
      authenticated: true,
      token: 'fresh-token',
      tokenParsed: {exp: 200},
      timeSkew: 0
    }

    syncAuthorizationCookie(keycloak)

    expect(set).toHaveBeenCalledWith(
      'Authorization',
      'Bearer fresh-token',
      expect.objectContaining({path: currentPath, sameSite: 'strict'})
    )
    expect(remove).toHaveBeenCalledWith('Authorization', {path: '/'})
    expect(remove).not.toHaveBeenCalledWith('Authorization', {path: currentPath})
  })

  it('leaves anonymous browser navigations unchanged', async () => {
    const keycloak = {
      authenticated: false,
      updateToken: jest.fn()
    }

    await refreshBrowserAuth(keycloak)

    expect(keycloak.updateToken).not.toHaveBeenCalled()
    expect(set).not.toHaveBeenCalled()
  })
})
