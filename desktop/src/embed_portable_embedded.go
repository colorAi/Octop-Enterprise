//go:build production && !darwin && !company

package main

import _ "embed"

// Linux and Windows production builds embed their matching portable runtime, so
// their release archive contains only the desktop binary.
//
//go:embed bundled/portable.zip
var embeddedPortable []byte
