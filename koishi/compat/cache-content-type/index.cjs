// Koa's response.type setter expects a CJS callable, not the upstream export object.
// Keep MIME conversion and the bounded cache in the pinned upstream implementation.
module.exports = require('cache-content-type-upstream').getType
