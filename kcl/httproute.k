"""
HTTPRoute (Gateway API) for homerun2-sound-catcher
"""

import labels

config = labels.config

_hostname = config.httpRouteHostname

httproute = {
    apiVersion: "gateway.networking.k8s.io/v1"
    kind: "HTTPRoute"
    metadata: {
        name: config.name
        namespace: config.namespace
        labels: labels.commonLabels
        if config.annotations or config.httpRouteAnnotations:
            annotations: {
                **config.annotations
                **config.httpRouteAnnotations
            }
    }
    spec: {
        parentRefs: [
            {
                name: config.httpRouteParentRefName
                if config.httpRouteParentRefNamespace:
                    namespace: config.httpRouteParentRefNamespace
            }
        ]
        if _hostname:
            hostnames: [_hostname]
        rules: [
            # The player page's event stream is one long response. Gateways
            # that cap request duration (Envoy's route default is 15s) cut it,
            # and the page reconnects and catches up, but with gaps. Set
            # httpRouteSseTimeout ("0s" = no limit) where the gateway supports
            # HTTPRoute timeouts.
            if config.httpRouteSseTimeout:
                {
                    matches: [
                        {
                            path: {
                                type: "PathPrefix"
                                value: "{}/api/events".format(config.basePath)
                            }
                        }
                    ]
                    timeouts: {
                        request: config.httpRouteSseTimeout
                    }
                    backendRefs: [
                        {
                            name: config.name
                            port: config.servicePort
                        }
                    ]
                }
            {
                matches: [
                    {
                        path: {
                            type: "PathPrefix"
                            value: config.basePath if config.basePath else "/"
                        }
                    }
                ]
                backendRefs: [
                    {
                        name: config.name
                        port: config.servicePort
                    }
                ]
            }
        ]
    }
} if config.httpRouteEnabled else None
