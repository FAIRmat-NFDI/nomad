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

import React, { useCallback, useContext, useEffect, useState } from 'react'
import PropTypes from 'prop-types'
import { isEmpty } from 'lodash'
import { useErrors } from '../errors'
import { useApi } from '../api'
import { useDataStore } from '../DataStore'
import {
  parseNomadUrl, resolveNomadUrl, resolveInternalRef, systemMetainfoUrl, createEntryUrl,
  urlEncodePath, urlJoin, relativizeNomadUrl
} from '../../utils'
import { apiBase } from '../../config'

const metainfoContext = React.createContext()

export const GlobalMetainfo = React.memo(function GlobalMetainfo({ children }) {
  const dataStore = useDataStore()
  const globalMetainfo = useMetainfo(systemMetainfoUrl)
  const { api } = useApi()
  const [allCustomMetainfos, setAllCustomMetainfos] = useState()

  const fetchAllCustomMetainfos = useCallback(async (refresh, query) => {
    if (allCustomMetainfos && !refresh) {
      return allCustomMetainfos
    }
    if (allCustomMetainfos) {
      // Refreshing.
      dataStore.resetEntryAndMetainfoCaches()
    }

    // TODO paginate?
    // TODO Only grab new ones?
    query = query || {}
    const response = await api.post(`entries/archive/query`, {
      owner: 'visible',
      query: {
        ...query,
        quantities: 'definitions.section_definitions',
        processed: true
      },
      required: {
        metadata: {
          entry_id: '*'
        }
      }
    })
    const foundCustomMetainfos = []
    for (const data of response.data) {
      try {
        const url = createEntryUrl(apiBase, data.upload_id, data.entry_id)
        const customMetainfo = await dataStore.getMetainfoAsync(url)
        foundCustomMetainfos.push(customMetainfo)
      } catch (error) {
        // Unparseable metainfo
      }
    }
    setAllCustomMetainfos(foundCustomMetainfos)
    return foundCustomMetainfos
  }, [api, dataStore, allCustomMetainfos, setAllCustomMetainfos])

  if (globalMetainfo) {
    globalMetainfo.fetchAllCustomMetainfos = fetchAllCustomMetainfos
  }
  return <metainfoContext.Provider value={globalMetainfo}>
    {children}
  </metainfoContext.Provider>
})
GlobalMetainfo.propTypes = {
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ])
}

export function useGlobalMetainfo() {
  return useContext(metainfoContext)
}

/**
 * React function for fetching a parsed metainfo object, given a url.
 * The url can be a string or a parsed Url object. If it is empty, null will be returned.
 * Note, this method always returns the whole data object. I.e. if the url specifies a
 * particular section definition, we return the metainfo data object which *contains* this
 * definition, not just the definition itself.
 */
export function useMetainfo(url) {
  const dataStore = useDataStore()
  const { raiseError } = useErrors()
  const [metainfo, setMetainfo] = useState()

  useEffect(() => {
    dataStore.getMetainfoAsync(url)
      .then(setMetainfo)
      .catch(error => {
        raiseError(error)
        setMetainfo(null)
      })
  }, [url, dataStore, raiseError, setMetainfo])

  return metainfo
}

/**
 * React function for fetching a parsed metainfo def, given a url.
 * The url can be a string or a parsed Url object. If it is empty, null will be returned.
 */
export function useMetainfoDef(url) {
  const dataStore = useDataStore()
  const { raiseError } = useErrors()
  const [metainfoDef, setMetainfoDef] = useState(null)

  useEffect(() => {
    if (!url) {
      setMetainfoDef(null)
    } else if (url.error) {
      raiseError(url.error)
      setMetainfoDef(null)
    } else {
      dataStore.getMetainfoDefAsync(url)
        .then(result => {
          setMetainfoDef(result)
        })
        .catch(error => {
          raiseError(error)
          setMetainfoDef(null)
        })
    }
  }, [url, setMetainfoDef, dataStore, raiseError])

  return metainfoDef
}

export const PackageMDef = 'nomad.metainfo.metainfo.Package'
export const SectionMDef = 'nomad.metainfo.metainfo.Section'
export const QuantityMDef = 'nomad.metainfo.metainfo.Quantity'
export const SubSectionMDef = 'nomad.metainfo.metainfo.SubSection'
export const CategoryMDef = 'nomad.metainfo.metainfo.Category'
export const AttributeMDef = 'nomad.metainfo.metainfo.Attribute'

export function quantityUsesFullStorage(def) {
  return def.repeats || def.variable || def.attributes?.length || def.flexible_unit
}

/**
 * Represents and manages schema data.
 *
 * It is responsible to create derived definition properties that are necessary for the
 * browser's function and to allow to resolve references to definitions.
 *
 * It allows to access schema data in two ways: via references and via qualified names
 * (e.g. based on packages name + section name).
 *
 * For package-based access, metainfo instances can be linked. A metainfo can have a parent.
 * Here metainfo instances will first try to resolve a qualified names with itself and it's
 * parent.
 *
 * For reference-based access, contexts can be used. A metainfo instance represents a single
 * root section (e.g. Package or Environment). URL fragments can be resolved here. The
 * rest of the reference URL can be used load respective resources with the context.
 */
export class Metainfo {
  /**
   * Constructs a Metainfo object. Note, this should only be invoked by the store.
   */
  constructor(parent, data, getMetainfoAsync, frozenMetainfoCache, externalInheritanceCache) {
    this._parent = parent
    this._data = data
    this._getMetainfoAsync = getMetainfoAsync
    this._frozenMetainfoCache = frozenMetainfoCache
    this._externalInheritanceCache = externalInheritanceCache
    this._url = data._url // the data url, always string
    this._parsedUrl = parseNomadUrl(data._url)

    this._defs = new Set()
    this._packageDefs = {}
    this._defsByNameCache = null
    this._packagePrefixCache = null
    this._rootSectionsCache = null

    // Initiate a single call to _parse, using a promise
    this._isParsed = false
    this._result = new Promise((resolve, reject) => {
      this._parse().then(resolve).catch(reject)
    })
  }

  /**
   * @returns All definitions as an Array.
   */
  getDefs() {
    if (this._parent) {
      return this._parent.getDefs().concat([...this._defs])
    } else {
      return [...this._defs]
    }
  }

  /**
   * @returns An object with all definition names as keys. Packages are excluded.
   *   The values are arrays with all the definitions that share the name.
   */
  getDefsByName() {
    if (this._defsByNameCache) {
      return this._defsByNameCache
    }
    this._defsByNameCache = this.getDefs().filter(def => def.m_def !== PackageMDef).reduce((result, def) => {
      result[def.name] = result[def.name] || []
      result[def.name].push(def)
      return result
    }, {})
    return this._defsByNameCache
  }

  /**
   * Gets a definition by its qualified name
   */
  getDefByQualifiedName(qualifiedName) {
    let pathSegments = qualifiedName.split('.')
    let pkg

    // Find the package by progressively checking `_packageDefs` for the most
    // specific package. We don't in general know where the package starts, as
    // it can be quite nested in the Python code.
    let splitIndex
    for (let i = pathSegments.length; i > 0; i--) {
      const candidateName = pathSegments.slice(0, i).join('.')
      if (this._packageDefs[candidateName]) {
        pkg = this._packageDefs[candidateName]
        if (pkg) {
          splitIndex = i
          break
        }
      }
    }
    // Handle entry-backed schema `entry_id:<schema-entry-id>.<section-name>`
    if (!pkg && this._parsedUrl.entryId) {
      pkg = Object.values(this._packageDefs).find(
        candidate => candidate._unique_id === `entry_id:${this._parsedUrl.entryId}`
      )
      splitIndex = 0
    }
    if (!pkg) return undefined
    pathSegments = pathSegments.slice(splitIndex)
    if (!pathSegments.length) return pkg

    // Try to find the section definition from the package root
    const sectionName = pathSegments.shift()
    const def = pkg?._sections?.[sectionName]
    if (!def) return undefined
    if (!pathSegments.length) return def

    // If there are still path segments to traverse, try to find the section
    // definition from the package inner sections. TODO: This is not yet
    // recursive, implement as necessary.
    for (const part of pathSegments) {
      for (const inner_section_def of def.inner_section_definitions) {
        if (part === inner_section_def.name) {
          return inner_section_def
        }
      }
    }

    return undefined
  }

  /**
   * Gets a definition by its path (starting with '#' or '/', i.e. from the 'root' of the metainfo data)
   */
  getDefByPath(path) {
    return resolveInternalRef(path, this._data)
  }

  /**
   * @returns An object with all package prefixes (the part of the name before the first ".")
   *   The values are arrays with all the packages that share the prefix.
   */
  getPackagePrefixes() {
    if (this._packagePrefixCache) {
      return this._packagePrefixCache
    }
    this._packagePrefixCache = this.getDefs()
      // The definition needs to point to a Package which has a name
      .filter(def => def.m_def === PackageMDef && def.name)
      .reduce((results, pkg) => {
        const packageName = pkg.name
        if (packageName !== '*') {
          const prefix = packageName.split('.')[0]
          results[prefix] = results[prefix] || {}
          results[prefix][packageName] = pkg
        }
        return results
      }, {})
    return this._packagePrefixCache
  }

  /**
   * @returns An array with all section definitions where not sub-section is using them
   *   as section definition.
   */
  getRootSectionDefinitions() {
    if (this._rootSectionsCache) {
      return this._rootSectionsCache
    }
    const sortDefs = defs => defs.sort((a, b) => a.name.localeCompare(b.name))
    this._rootSectionsCache = sortDefs(this.getDefs().filter(def => (
      def.m_def === SectionMDef && !def.extends_base_section && def._parentSections.length === 0)
    ))
    return this._rootSectionsCache
  }

  getEntryArchiveDefinition() {
    // TODO this is a super wasteful implementation
    const entryArchiveDefinition = this.getRootSectionDefinitions().find(def => def.name === 'EntryArchive')
    return entryArchiveDefinition
  }

  /**
   * Parses the metainfo data
   */
  async _parse() {
    // Parse data
    if (this._data.packages) {
      // Initial parsing
      for (const pkg of this._data.packages) {
        this._addDefIdsToCache(pkg)
      }
      // Main parsing
      let pkgIndex = 0
      for (const pkg of this._data.packages) {
        await this._addPackage(pkg, null, this._data, 'packages', pkgIndex++)
      }
    }
    if (this._data.definitions) {
      // Initial parsing
      this._addDefIdsToCache(this._data.definitions)
      // Main parsing
      const entryId = this._parsedUrl.entryId
      await this._addPackage(this._data.definitions, `entry_id:${entryId}`, this._data, 'definitions')
    }
    this._isParsed = true
    return this
  }

  _addDefIdsToCache(def) {
    // Used for an initial, synchronous shallow parsing, which adds all encountered definition_ids
    // to this._frozenMetainfoCache, so they can be used when resolving references during the
    // main parsing step.
    if (def.definition_id) {
      if (!this._frozenMetainfoCache[def.definition_id]) {
        this._frozenMetainfoCache[def.definition_id] = def
      }
    }
    for (const subDefs of [def.category_definitions, def.section_definitions, def.inner_section_definitions, def.quantities]) {
      if (subDefs) {
        for (const subDef of subDefs) {
          this._addDefIdsToCache(subDef)
        }
      }
    }
  }

  async _addDef(def) {
    // only add if not already added
    if (this._defs.has(def)) {
      return
    }
    def.more = def.more || {}
    def.categories = await this.resolveDefinitionList(def.categories || [])
    this._defs.add(def)
    this._defsByNameCache = null
  }

  async _initSection(sectionDef) {
    if (sectionDef._allBaseSections !== undefined) {
      return sectionDef
    }

    sectionDef.base_sections = sectionDef.base_sections || []
    sectionDef.quantities = sectionDef.quantities || []
    sectionDef.sub_sections = sectionDef.sub_sections || []
    sectionDef.inner_section_definitions = sectionDef.inner_section_definitions || []
    sectionDef._allBaseSections = []
    sectionDef._allInternalInheritingSections = []
    sectionDef._parentSections = []
    sectionDef._parentSubSections = []

    const resolvedBaseSections = []
    for (const baseSectionRef of sectionDef.base_sections) {
      const baseSection = await this.resolveDefinition(baseSectionRef)
      resolvedBaseSections.push(baseSection)
      if (!sectionDef.extends_base_section) {
        await this._initSection(baseSection)
        sectionDef._allBaseSections.push(baseSection)
        baseSection._allBaseSections.forEach(baseBaseSection => sectionDef._allBaseSections.push(baseBaseSection))
        if (typeof baseSectionRef === 'string' && (baseSectionRef.startsWith('#') || baseSectionRef.startsWith('/'))) {
          // Internal base section link (both this and the base section defined in the same metainfo data)
          if (!baseSection._allInternalInheritingSections.includes(sectionDef)) {
            baseSection._allInternalInheritingSections.push(sectionDef)
          }
        } else {
          // External base section link. These are stored in a separate cache in the store.
          const baseSectionUrl = getUrlFromDefinition(baseSection)
          let externalInheritingSections = this._externalInheritanceCache[baseSectionUrl]
          if (!externalInheritingSections) {
            externalInheritingSections = []
            this._externalInheritanceCache[baseSectionUrl] = externalInheritingSections
          }
          if (!externalInheritingSections.includes(sectionDef)) {
            externalInheritingSections.push(sectionDef)
          }
        }
      }
    }
    sectionDef.base_sections = resolvedBaseSections
    return sectionDef
  }

  async _createAllAttributes(sectionDef) {
    const allSectionAttributes = {}
    const allQuantityAttributes = {}
    function createAddAttributes(inherited) {
      return (sectionDef) => {
        function createAddAttribute(m_def, allAttributes, inherited) {
          return (attribute) => {
            if (!inherited && allAttributes[attribute.name]) {
              attribute._overwritten = true
            }
            attribute.m_def = m_def
            allAttributes[attribute.name] = attribute
          }
        }
        sectionDef.attributes?.forEach(createAddAttribute(AttributeMDef, allSectionAttributes, inherited))
        sectionDef.quantities.forEach(quantitiy => {
          allQuantityAttributes[quantitiy.name] = allQuantityAttributes[quantitiy.name] || {}
          const quantityAttributes = allQuantityAttributes[quantitiy.name]
          quantitiy.attributes?.forEach(createAddAttribute(
            AttributeMDef, quantityAttributes, quantitiy._parent !== sectionDef))
        })
      }
    }
    sectionDef = await this._initSection(sectionDef)
    const reversedBaseSections = [...sectionDef._allBaseSections].reverse()
    reversedBaseSections.forEach(createAddAttributes(true))
    createAddAttributes(false)(sectionDef)

    sectionDef._allAttributes = Object.keys(allSectionAttributes).map(key => allSectionAttributes[key])
    sectionDef.quantities.forEach(quantitiy => {
      const allAttritbutes = allQuantityAttributes[quantitiy.name]
      quantitiy._allAttributes = Object.keys(allAttritbutes).map(key => allAttritbutes[key])
    })
  }

  async _getAllProperties(sectionDef) {
    const results = {}
    function createAddProperties(inherited) {
      return (sectionDef) => {
        function createAddProperty(m_def) {
          return (property) => {
            if (!inherited && results[property.name]) {
              property._overwritten = true
            }
            property.m_def = m_def
            results[property.name] = property
          }
        }
        sectionDef.quantities.forEach(createAddProperty(QuantityMDef))
        sectionDef.sub_sections.forEach(createAddProperty(SubSectionMDef))
      }
    }
    sectionDef = await this._initSection(sectionDef)
    const reversedBaseSections = [...sectionDef._allBaseSections].reverse()
    reversedBaseSections.forEach(createAddProperties(true))
    createAddProperties(false)(sectionDef)
    return Object.keys(results).map(key => results[key])
  }

  async _addSection(pkg, sectionDef, parentDef, parentProperty, parentIndex) {
    this._rootSectionsCache = null
    sectionDef.m_def = SectionMDef
    sectionDef._parent = parentDef
    sectionDef._parentProperty = parentProperty
    sectionDef._parentIndex = parentIndex
    pkg._sections[sectionDef.name] = sectionDef
    await this._initSection(sectionDef)
    sectionDef._qualifiedName = parentDef ? `${parentDef._qualifiedName || parentDef._unique_id || parentDef.name}.${sectionDef.name}` : sectionDef.name
    sectionDef._package = pkg

    let index = 0
    for (const innerSectionDef of sectionDef.inner_section_definitions) {
      await this._addSection(pkg, innerSectionDef, sectionDef, 'inner_section_definitions', index)
      index++
    }

    const addPropertiesFromSections = async sections => {
      for (const ref of sections) {
        const extendingSectionDef = await this.resolveDefinition(ref)
        if (extendingSectionDef.quantities) {
          sectionDef.quantities.push(...extendingSectionDef.quantities)
        }
        if (extendingSectionDef.sub_sections) {
          sectionDef.sub_sections.push(...extendingSectionDef.sub_sections)
        }
      }
    }
    sectionDef.extending_sections = sectionDef.extending_sections || []
    await addPropertiesFromSections(sectionDef.extending_sections)
    if (!sectionDef.extends_base_section) {
      await this._addDef(sectionDef)
    }

    sectionDef._allProperties = await this._getAllProperties(sectionDef)
    sectionDef._properties = {}

    // Do for new properties (i.e. defined in THIS section, not inherited from base sections)
    const addNewProperty = async (property, parentProperty, index) => {
      property._section = sectionDef
      property._parentProperty = parentProperty
      property._parentIndex = index
      property._qualifiedName = `${sectionDef._qualifiedName}.${property.name}`
      property._package = pkg
      property._parent = sectionDef
      for (const attribute of (property?.attributes || [])) {
        attribute._parent = property
      }
      await this._addDef(property)
      if (property.m_def === QuantityMDef) {
        property.shape = property.shape || []
        if (isReference(property)) {
          const referencedDefinition = await this.resolveDefinition(property.type.type_data)
          property.type._referencedDefinition = referencedDefinition
        }
      } else if (property.m_def === SubSectionMDef) {
        property.sub_section = await this.resolveDefinition(property.sub_section)
        const subSectionsSectionDef = property.sub_section
        await this._initSection(subSectionsSectionDef)
        subSectionsSectionDef._parentSections.push(sectionDef)
        subSectionsSectionDef._parentSubSections.push(property)
        property._section = sectionDef
      }
    }
    for (const attribute of (sectionDef?.attributes || [])) {
      attribute._parent = sectionDef
    }
    for (const def of sectionDef.quantities) {
      await addNewProperty(def, 'quantities', index)
    }
    for (const def of sectionDef.sub_sections) {
      await addNewProperty(def, 'sub_sections', index)
    }

    // Do for all properties (new + inherited)
    for (const property of sectionDef._allProperties) {
      sectionDef._properties[property.name] = property
    }

    await this._createAllAttributes(sectionDef)
  }

  async _addPackage(pkg, unique_id, pkgParentData, parentProperty, parentIndex) {
    this._packagePrefixCache = null
    pkg.m_def = PackageMDef
    if (unique_id) {
      pkg._unique_id = unique_id
    }
    const packageName = pkg.name || '*'
    this._packageDefs[packageName] = pkg
    for (const alias of pkg?.aliases || []) {
      this._packageDefs[alias] = pkg
    }
    await this._addDef(pkg)

    pkg._pkgParentData = pkgParentData
    pkg._parentProperty = parentProperty
    pkg._parentIndex = parentIndex
    pkg._sections = {}
    pkg.category_definitions = pkg.category_definitions || []
    pkg.section_definitions = pkg.section_definitions || []
    for (const categoryDef of pkg.category_definitions) {
      categoryDef.m_def = CategoryMDef
      categoryDef._qualifiedName = `${pkg._unique_id || pkg.name}.${categoryDef.name}`
      categoryDef._package = pkg
      await this._addDef(categoryDef)
    }

    let index = 0
    for (const sectionDef of pkg.section_definitions) {
      await this._addSection(pkg, sectionDef, pkg, 'section_definitions', index++)
    }
  }

  path(nameOrDef) {
    let def
    if (typeof nameOrDef === 'string') {
      const defsByName = this.getDefsByName()
      def = defsByName[nameOrDef] && defsByName[nameOrDef].find(def => true)
    } else {
      def = nameOrDef
    }

    if (!def) {
      return null
    }

    if (def.m_def === SubSectionMDef) {
      def = def.sub_section
    }

    if (def.m_def === CategoryMDef) {
      return `${def._package.name.split('.')[0]}/category_definitions@${def._qualifiedName}`
    }

    const path = []
    const visitedSubSections = new Set()
    while (def) {
      if (def.m_def === SectionMDef) {
        // See if there is a sub-section that uses this section. If there is only one
        // sub-section, we use this sub-section to represent the section def, because
        // typically it is more desirable to show the section within the overall hierarchy
        // than just the section def in its defining package.
        // We also remember visited sub-sections to avoid circles.
        const parentSubSections = def._parentSubSections && def._parentSubSections.filter(
          subSection => !visitedSubSections.has(subSection))
        if (parentSubSections.length === 1) {
          def = parentSubSections[0]
          visitedSubSections.add(def)
        }
      }

      if (def._parent?.m_def === PackageMDef) {
        // For package contents, we need to annotate the path segment corresponding to
        // the package adaptor in the Metainfo browser.
        if (def.m_def === SectionMDef) {
          path.push(`section_definitions@${def._qualifiedName}`)
        } else if (def.m_def === CategoryMDef) {
          path.push(`category_definitions@${def._qualifiedName}`)
        }
      } else if (def.m_def === PackageMDef) {
        // For packages, we use only the root package name. This merges you all the
        // sub-pacakges in one. This is just how the Metainfo adaptor hierarchy is setup.
        path.push(def.name.split('.')[0])
      } else {
        path.push(def.name)
      }

      if (def._qualifiedName === 'nomad.datamodel.datamodel.EntryArchive') {
        // If definitions are contained in EntryArchive, we adapt to show them below
        // the root section EntryArchive instead of its package nomad.
        path[path.length - 1] = def._qualifiedName
        def = null
      } else {
        def = def._parent
      }

      while (def && def.extends_base_section) {
        def = def.base_sections[0]
      }
    }
    return path.reverse().join('/')
  }

  async resolveDefinitionList(references) {
    const result = []
    for (const reference of references) {
      result.push(await this.resolveDefinition(reference))
    }
    return result
  }

  /**
   * Resolves a reference found inside this metainfo object
   */
  async resolveDefinition(reference, raise = true) {
    if (typeof reference !== 'string') {
      // already resolved
      return reference
    }
    let resolvedUrl
    if (reference.includes('@')) {
      const versionHash = reference.split('@')[1]
      const frozenDef = this._frozenMetainfoCache[versionHash]
      if (frozenDef) {
        // Found in cache
        return frozenDef
      } else {
        // Not found in cache. Fetch it.
        resolvedUrl = resolveNomadUrl(reference, this._parsedUrl)
        await this._getMetainfoAsync(resolvedUrl, raise)
        return this._frozenMetainfoCache[versionHash] // Should now be in the cache
      }
    }
    if (reference.startsWith('#') || reference.startsWith('/')) {
      // Local path reference
      return this.getDefByPath(reference)
    }
    resolvedUrl = resolveNomadUrl(reference, this._parsedUrl)
    if (resolvedUrl.entryId && resolvedUrl.qualifiedName) {
      // Reference to entry metainfo
      const metainfo = await this._getMetainfoAsync(resolvedUrl, raise)
      return metainfo.getDefByQualifiedName(resolvedUrl.qualifiedName)
    } else if (resolvedUrl.qualifiedName) {
      // Reference to the system metainfo, using qualified name
      const systemMetainfo = this._url === systemMetainfoUrl ? this : await this._getMetainfoAsync(systemMetainfoUrl, raise)
      return systemMetainfo.getDefByQualifiedName(resolvedUrl.qualifiedName)
    } else if (resolvedUrl.entryId) {
      // Reference to entry metainfo
      const metainfo = await this._getMetainfoAsync(resolvedUrl, raise)
      return metainfo.getDefByPath(resolvedUrl.path)
    }
    throw new Error(`Bad reference encountered: ${reference}`)
  }
}

/**
 * Converts a reference given in the /section/<index>/subsection format (used
 * in the metainfo) to the /section:<index>/subsection format (used by the
 * archive browser).
 * @param {*} ref The reference to convert.
 */
export function refPath(ref) {
  try {
    if (ref.startsWith('#')) {
      ref = ref.slice(1)
    }
    const segments = ref.split('/').filter(segment => segment !== '')
    const reducer = (current, segment) => {
      return isNaN(segment) ? `${current}/${segment}` : `${current}:${segment}`
    }
    return segments.reduce(reducer)
  } catch (e) {
    console.log('could not convert the path: ' + ref)
    throw e
  }
}

export function isReference(property) {
  return property.type && (
    property.type.type_kind === 'reference' ||
    property.type.type_kind === 'quantity_reference')
}

/**
 * @param {*} def A section definition.
 * @returns True, if sections of the given section def are editable.
 */
export function isEditable(def) {
  return !!def._allProperties.find(prop => prop.m_annotations?.eln) || !!def.m_annotations?.eln
}

export function removeSubSection(section, subSectionDef, index) {
  if (subSectionDef.repeats) {
    section[subSectionDef.name].splice(index, 1)
  } else {
    section[subSectionDef.name] = undefined
    delete section[subSectionDef.name]
  }
}

/**
 * Given a definition, compute its url (string). Optionally, you can specify relativeTo, an
 * object of the form {deploymentUrl, uploadId, entryId} (containing the first, the two first,
 * or all three atributes, depending on what you want the url to be relative to). If relativeTo
 * is left out, we return an absolute url. You may also specify preferMainfile = true if you
 * want the url to use the mainfile rather than the entryId when possible (more humanly readable).
 */
export function getUrlFromDefinition(definition, relativeTo = null, preferMainfile = false) {
  const pkg = definition.m_def === PackageMDef ? definition : definition._package
  const metainfo = pkg._pkgParentData._metainfo
  if (!metainfo._parsedUrl.entryId && relativeTo?.deploymentUrl === metainfo._parsedUrl.deploymentUrl) {
    return definition._qualifiedName
  }
  let parentUrl
  switch (definition.m_def) {
    case PackageMDef: {
      const entryId = metainfo._parsedUrl.entryId
      if (entryId) {
        // Custom metainfo
        let rv
        if (relativeTo) {
          rv = relativizeNomadUrl(
            metainfo._parsedUrl, relativeTo.deploymentUrl, relativeTo.uploadId, relativeTo.entryId)
        } else {
          rv = metainfo._url
        }
        if (preferMainfile) {
          const mainfile = metainfo._data.metadata.mainfile
          rv = rv.replace(`/archive/${entryId}`, `/raw/${urlEncodePath(mainfile)}`)
        }
        return rv
      }
      // System metainfo
      return urlJoin(metainfo._url, 'packages', definition._parentIndex)
    }
    case SectionMDef:
      parentUrl = getUrlFromDefinition(definition._parent, relativeTo, preferMainfile)
      break
    case SubSectionMDef:
    case QuantityMDef:
      parentUrl = getUrlFromDefinition(definition._section, relativeTo, preferMainfile)
      break
    case CategoryMDef:
      parentUrl = getUrlFromDefinition(definition._package, relativeTo, preferMainfile)
      break
    default:
      throw new Error('Could not get url from definition: bad m_def')
  }
  const ref = `${parentUrl}/${definition._parentProperty}`
  if (!isNaN(definition._parentIndex)) {
    return `${ref}/${definition._parentIndex}`
  }
  return ref
}

/**
 * Given a definition, gets the metainfo object in which it is defined.
 */
export function getMetainfoFromDefinition(definition) {
  if (definition._pkgParentData) return definition._pkgParentData._metainfo
  return getMetainfoFromDefinition(definition._package || definition._parent || definition._section)
}

/**
 * Allows to traverse a given section through all its sub-sections.
 * @param {Object} section The section to traverse
 * @param {Object} definition The definition of the section
 * @param {str} path The archive path to the section
 * @param {function} callback The callback that is called on each section
 */
export async function traverse(section, definition, path, callback) {
  callback(section, definition, path)

  // Loop through all subsection definitions
  for (const subSectionDef of definition._allProperties.filter(prop => prop.m_def === SubSectionMDef)) {
    let sectionDef = subSectionDef.sub_section

    // Find all subsection instances
    let subSections = []
    if (!subSectionDef.repeats) {
      const subSection = section[subSectionDef.name]
      if (subSection) {
        subSections = [subSection]
      }
    } else {
      subSections = section[subSectionDef.name] || []
    }

    // For each found instance, check for m_def definition override in the data
    // and trigger the recursion.
    let index = 0
    for (const subSection of subSections) {
      let childPath = `${path}/${subSectionDef.name}`
      if (subSectionDef.repeats) {
        childPath = `${childPath}/${index}`
      }
      // Here the final definition is resolved based on the presence of an
      // m_def. TODO: m_defs that point to a definition that is not within the
      // same metainfo that the original definition came from are not here
      // properly resolved.
      if (subSection.m_def && !subSection.m_def.startsWith('../upload/')) {
        const metainfo = getMetainfoFromDefinition(definition)
        sectionDef = await metainfo.resolveDefinition(subSection.m_def)
      }
      await traverse(subSection, sectionDef, childPath, callback)
      ++index
    }
  }
}

/**
 * Generator for efficiently returning all quantities of a section. Note that
 * speed becomes quite important with very large metainfo.
 *
 * @param {Object} definition The definition of the section
 * @param {str} prefix The current path
 * @param {array} branch Container that is populated with with add secion
 * definitions that have been ancountered on a certain path through the
 * metainfo. This information is used for avoiding loops that may be
 * present in the metainfo.
 *
 */
export function * getQuantities(definition, prefix, branch, repeats = false) {
  if (!branch) {
    branch = new Set()
  }
  if (definition._allProperties) {
    for (const def of definition._allProperties) {
      const quantityName = prefix ? `${prefix}.${def.name}` : def.name
      if (def.m_def === 'nomad.metainfo.metainfo.Quantity') {
        yield [def, quantityName, repeats || !isEmpty(def.shape)]
      } else {
        yield * getQuantities(def, quantityName, new Set(branch), repeats || def.repeats)
      }
    }
  }
  const sub_section_def = definition.sub_section
  if (sub_section_def) {
    if (branch.has(sub_section_def)) return
    branch.add(sub_section_def)
    yield * getQuantities(sub_section_def, prefix, branch, repeats)
  }
}

/**
 * Constructs a graph from and with the definition. The graph will contain the given nodes,
 * all its outgoing and incomming references, the parents up to root (for sections and categories)
 *
 * @param {Object} def
 */
export function vicinityGraph(def) {
  const nodesMap = {}
  const nodes = []
  const edges = []
  const dx = 100
  const dy = 75

  function addEdge(from, to, def) {
    const edge = {
      def: def,
      source: from,
      target: to,
      value: 1
    }
    edges.push(edge)
  }

  function addNode(def, more, id) {
    id = id || (d => d._qualifiedName)
    const { recursive, x, y, i } = more || {}

    const key = id(def)
    if (nodesMap[key]) {
      return nodesMap[key]
    }

    const node = {
      id: key,
      def: def,
      x: x,
      y: y,
      i: i
    }

    nodes.push(node)
    nodesMap[key] = node

    if (recursive) {
      if (def.m_def === SectionMDef) {
        def._parentSections.forEach(parentSection => {
          const parent = addNode(parentSection, {
            recursive: true,
            x: x - dx,
            y: y,
            i: i + 1
          })
          addEdge(node, parent, {})
        })
        const references = def.quantities.filter(quantity => quantity.type.type_kind === 'reference')
        const layoutMiddle = (references.length - 1) * dx / 2
        const metainfo = getMetainfoFromDefinition(def)
        references.forEach((reference, i) => {
          try {
            const referencedSectionDef = resolveInternalRef(reference.type.type_data, metainfo)
            const referenced = addNode(
              referencedSectionDef,
              { x: x + i * dx - layoutMiddle, y: y + dy, i: i },
              () => reference._qualifiedName)
            addEdge(node, referenced, reference)
          } catch (error) {
            // Ignore for now. External ref?
          }
        })
      } else if (def.m_def === QuantityMDef) {
        const section = addNode(def._section, {
          recursive: true,
          x: x - dx,
          y: y,
          i: i + 1
        })
        addEdge(node, section, {})
      }

      if (def.categories) {
        const layoutMiddle = (def.categories.length - 1) * dx / 2
        def.categories.forEach((categoryDef, i) => {
          const category = addNode(categoryDef, {
            recursive: true,
            x: x + i * dx - layoutMiddle,
            y: y - dy,
            i: i
          })
          addEdge(node, category, {})
        })
      }
    }

    return node
  }

  addNode(def, { recursive: true, x: 0, y: 0, i: 0 })

  return {
    nodes: nodes,
    links: edges
  }
}
