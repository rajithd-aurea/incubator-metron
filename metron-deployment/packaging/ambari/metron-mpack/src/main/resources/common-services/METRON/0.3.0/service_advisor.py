#!/usr/bin/env ambari-python-wrap
"""
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import os
import fnmatch
import imp
import socket
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STACKS_DIR = os.path.join(SCRIPT_DIR, '../../../../../stacks/')
PARENT_FILE = os.path.join(STACKS_DIR, 'service_advisor.py')

try:
    with open(PARENT_FILE, 'rb') as fp:
        service_advisor = imp.load_module('service_advisor', fp, PARENT_FILE, ('.py', 'rb', imp.PY_SOURCE))
except Exception as e:
    traceback.print_exc()
    print "Failed to load parent"

class METRON030ServiceAdvisor(service_advisor.ServiceAdvisor):

    def getServiceComponentLayoutValidations(self, services, hosts):

        componentsListList = [service["components"] for service in services["services"]]
        componentsList = [item["StackServiceComponents"] for sublist in componentsListList for item in sublist]

        metronParsersHost = self.getHosts(componentsList, "METRON_PARSERS")[0]
        metronEnrichmentMaster = self.getHosts(componentsList, "METRON_ENRICHMENT_MASTER")[0]
        metronIndexingHost = self.getHosts(componentsList, "METRON_INDEXING")[0]
        metronEnrichmentMysqlServer = self.getHosts(componentsList, "METRON_ENRICHMENT_MYSQL_SERVER")[0]

        kafkaBrokers = self.getHosts(componentsList, "KAFKA_BROKER")
        stormSupervisors = self.getHosts(componentsList,"SUPERVISOR")

        items = []

        #Metron Must Co-locate with KAFKA_BROKER and STORM_SUPERVISOR
        if metronParsersHost not in kafkaBrokers:
            message = "Metron must be colocated with an instance of KAFKA BROKER"
            items.append({ "type": 'host-component', "level": 'ERROR', "message": message, "component-name": 'METRON_PARSERS', "host": metronParsersHost })

        if metronParsersHost not in stormSupervisors:
            message = "Metron must be colocated with an instance of STORM SUPERVISOR"
            items.append({ "type": 'host-component', "level": 'WARN', "message": message, "component-name": 'METRON_PARSERS', "host": metronParsersHost })

        if metronParsersHost != metronEnrichmentMaster:
            message = "Metron Enrichment Master must be co-located with Metron Parsers on {0}".format(metronParsersHost)
            items.append({ "type": 'host-component', "level": 'ERROR', "message": message, "component-name": 'METRON_ENRICHMENT_MASTER', "host": metronEnrichmentMaster })

        if metronParsersHost != metronIndexingHost:
            message = "Metron Indexing must be co-located with Metron Parsers on {0}".format(metronParsersHost)
            items.append({ "type": 'host-component', "level": 'ERROR', "message": message, "component-name": 'METRON_INDEXING', "host": metronIndexingHost })

        if metronParsersHost != metronEnrichmentMysqlServer:
            message = "Metron MySQL Server must be co-located with Metron Parsers on {0}".format(metronParsersHost)
            items.append({ "type": 'host-component', "level": 'ERROR', "message": message, "component-name": 'METRON_ENRICHMENT_MYSQL_SERVER', "host": metronEnrichmentMysqlServer })

        return items

    def getServiceConfigurationsValidationItems(self, configurations, recommendedDefaults, services, hosts):

        # validate recommended properties in storm-site
        siteName = "storm-site"
        method = self.validateSTORMSiteConfigurations
        items = self.validateConfigurationsForSite(configurations, recommendedDefaults, services, hosts, siteName, method)

        return items

    def getServiceConfigurationRecommendations(self, configurations, clusterData, services, hosts):
        #Suggest mysql server hostname
        mySQLServerHost = self.getComponentHostNames(services, "METRON", "METRON_ENRICHMENT_MYSQL_SERVER")[0]
        putMetronEnvProperty = self.putProperty(configurations, "metron-env", services)
        putMetronEnvProperty("mysql_host",mySQLServerHost)

        #Suggest Storm Rest URL
        if "storm-site" in services["configurations"]:
            stormUIServerHost = self.getComponentHostNames(services, "STORM", "STORM_UI_SERVER")[0]
            stormUIServerPort = services["configurations"]["storm-site"]["properties"]["ui.port"]
            stormUIServerURL = stormUIServerHost + ":" + stormUIServerPort
            putMetronEnvProperty = self.putProperty(configurations, "metron-env", services)
            putMetronEnvProperty("storm_rest_addr",stormUIServerURL)

            storm_site = services["configurations"]["storm-site"]["properties"]
            putStormSiteProperty = self.putProperty(configurations, "storm-site", services)

            for property, desired_value in self.getSTORMSiteDesiredValues().iteritems():
                if property not in storm_site:
                    putStormSiteProperty(property, desired_value)
                elif  property == "topology.classpath" and storm_site[property] != desired_value:
                    topololgyClasspath = storm_site[property]
                    #check that desired values exist in topology.classpath. append them if they do not
                    for path in desired_value.split(':'):
                        if path not in topololgyClasspath:
                            topololgyClasspath += ":" + path
                    putStormSiteProperty(property,topololgyClasspath)

    def validateSTORMSiteConfigurations(self, properties, recommendedDefaults, configurations, services, hosts):

        storm_site = properties
        validationItems = []

        for property, desired_value in self.getSTORMSiteDesiredValues().iteritems():
            if property not in storm_site :
                message = "Metron requires this property to be set to the recommended value of " + desired_value
                item = self.getErrorItem(message) if property == "topology.classpath" else self.getWarnItem(message)
                validationItems.append({"config-name": property, "item": item})
            elif  storm_site[property] != desired_value:
                topologyClasspath = storm_site[property]
                for path in desired_value.split(':'):
                    if path not in topologyClasspath:
                        message = "Metron requires this property to contain " + desired_value
                        item = self.getErrorItem(message)
                        validationItems.append({"config-name": property, "item": item})

        return self.toConfigurationValidationProblems(validationItems, "storm-site")

    def getSTORMSiteDesiredValues(self):

        storm_site_desired_values = {
            "topology.classpath" : "/etc/hbase/conf:/etc/hadoop/conf"
        }

        return storm_site_desired_values

