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
import { createTheme } from '@material-ui/core'
import { isPlainObject, isNil, forOwn } from 'lodash'

/**
 * Used to normalized the given URL into an absolute form which starts with
 * protocol, host and port.
 *
 * @param {*} url The url to convert
 * @param {*} base The URL base address. Contains the protocol, host and port. Defaults to
 *   current window origin.
 * @param {*} protocol The desired protocol. By default the protocol in 'base'
 *   is used.
 * @returns Absolute url as a string
 */
 export function urlAbs(url, base = window.location.origin, protocol) {
  let absUrl = new URL(url, base).href

  // Convert protocol if one is given
  if (protocol) {
    const oldProtocol = absUrl.split('//', 1)[0]
    absUrl = `${protocol}${absUrl.slice(oldProtocol.length)}`
  }

  return absUrl
}

/**
 * Returns a normalized version of an UI config model. The following changes are applied
 * in the normalized version:
 *
 *  - The 'include' and 'exclude' attributes have been used to filter and order the
 *    options object and the key is automatically included as an option property.
 *
 * @param {object} config The original UI config.
 * @return Normalized version of the UI config model.
 */
export function normalizeConfig(config) {
    if (isNil(config)) return config

    function normalize(obj) {
      for (const [key, value] of Object.entries(obj)) {
        if (isPlainObject(value)) {
          normalize(value)

          // Normalize the options
          if (!value.options) continue
          const include = value.include || (value.options && Object.keys(value.options))
          const options = include
            ? include
              .filter(key => !value?.exclude?.includes(key))
              .map(key => ({key, ...value.options[key]}))
            : []

          const config = {options: Object.fromEntries(options.map(option => [option.key, option]))}

          // Carry over all additional fields
          forOwn(value, (v, k) => {
            if (k !== 'options' && k !== 'include' && k !== 'exclude') {
              config[k] = v
            }
          })

          obj[key] = config
        }
      }
    }

    // Recursively normalize the config
    normalize(config)

    return config
}

window.nomadEnv = window.nomadEnv || {}
export const version = window.nomadEnv.version
export const description = window.nomadEnv.description
export const appBase = urlAbs(window.nomadEnv.appBase.replace(/\/$/, ''))
export const apiBase = `${appBase}/api`
export const docsBase = `${appBase}/docs`
export const northBase = urlAbs(window.nomadEnv.northBase)
export const northEnabled = window.nomadEnv.northEnabled || false
export const enableNewHubApi = window.nomadEnv.enableNewHubApi || false
export const guiBase = process.env.PUBLIC_URL
export const ui = normalizeConfig(window.nomadEnv.ui)
export const entry_points = window.nomadEnv?.plugins?.entry_points
export const apps = Object.values(ui?.apps?.options || [])
Object.values(entry_points || [])
  .filter(entry_point => entry_point.entry_point_type === 'app')
  .forEach(entry_point => {
    apps.push(entry_point.app)
  })
export const servicesUploadLimit = window.nomadEnv.servicesUploadLimit
export const keycloakBase = window.nomadEnv.keycloakBase
export const keycloakRealm = window.nomadEnv.keycloakRealm
export const keycloakClientId = window.nomadEnv.keycloakClientId
export const debug = window.nomadEnv.debug || false
export const encyclopediaBase = window.nomadEnv.encyclopediaBase
export const oasis = window.nomadEnv.oasis || false
export const globalLoginRequired = window.nomadEnv.globalLoginRequired || false
export const appTokenMaxExpiresIn = window.nomadEnv.appTokenMaxExpiresIn || "2023-10-25"
export const uploadMembersGroupSearchEnabled = window.nomadEnv.uploadMembersGroupSearchEnabled || false
export const dataciteEnabled = window.nomadEnv.dataciteEnabled || false
export const termsOfServiceURL = window.nomadEnv.termsOfServiceURL
export const footerLinks = window.nomadEnv.footerLinks
export const email = 'support@nomad-lab.eu'
export const maxLogsToShow = 50
export const schemaSeparator = '#'
export const dtypeSeparator = '#'
export const yamlSchemaPrefix = 'entry_id:'
export const uploadPagination = window.nomadEnv.uploadPagination
export const entryPagination = window.nomadEnv.entryPagination

window.nomadArtifacts = window.nomadArtifacts || {}
export const searchQuantities = window.nomadArtifacts.searchQuantities
export const metainfo = window.nomadArtifacts.metainfo
export const parserMetadata = window.nomadArtifacts.parserMetadata
export const toolkitMetadata = window.nomadArtifacts.toolkitMetadata
export const exampleUploads = {}
Object.values(entry_points || [])
  .filter(entry_point => entry_point.entry_point_type === 'example_upload')
  .forEach(entry_point => {
    exampleUploads[entry_point.category] = {...exampleUploads[entry_point.category], [entry_point.id]: entry_point}
  })
export const northTools = window.nomadArtifacts.northTools
export const unitList = window.nomadArtifacts.unitList
export const unitPrefixes = window.nomadArtifacts.unitPrefixes

export const nomadPrimaryColor = {
  main: '#2A4CDF',
  light: '#7F94EC',
  dark: '#192E86',
  veryLight: '#eaedfc'
}

export const nomadSecondaryColor = {
  main: '#008A67',
  light: '#80C583',
  dark: '#00533e',
  veryLight: '#CCE8E1'
}

export const nomadFontFamily = [
  'Titillium Web',
  'sans-serif'
].join(',')

export const nomadTheme = createTheme({
  typography: {
    useNextVariants: true,
    fontFamily: nomadFontFamily,
    fontWeightRegular: 400,
    fontWeightMedium: 600
  },
  palette: {
    primary: nomadPrimaryColor,
    secondary: nomadSecondaryColor
  },
  overrides: {
    // This is used to inject global css styles through the CssBaseline
    // component, see: https://v4.mui.com/howto/customization/globals/#global-css
    MuiCssBaseline: {
      '@global': {
        '.react-grid-item.react-grid-placeholder': {
            backgroundColor: nomadSecondaryColor.main
        },
        // This rule is overwritten. The normalization was added quite late into
        // the app and the 'box-sizing: inherit' -rule would mess up the layout
        // quite a bit.
        '*, *::before, *::after': {
          boxSizing: 'revert'
        }
      }
    },
    MuiTooltip: {
      tooltip: {
        fontWeight: 'normal',
        fontSize: '0.75rem'
      }
    },
    MuiTableRow: {
      root: {
        '&:last-child td': {
          borderBottom: 0
        }
      }
    }
  }
})

export const formatQuantity = (x) => {
  const parts = x.toString().split('.')
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return parts.join('.')
}

export function normalizeDisplayValue(value) {
  if (value === 'not processed' || value === 'unavailable') {
    return '-'
  }
  return value
}

/**
 * The range of electronic energies in eV.
 */
export const electronicRange = [-5, 10]
/**
 * The default date format.
 */
export const dateFormat = 'dd/MM/yyyy'
/**
 * Warning shown when energy values could not be normalized.
 */
export const msgNormalizationWarning = `
Energy reference could not be found: energies have an unknown shift with
respect to the highest occupied energy.
`
