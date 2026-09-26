const asset = (name) => `/static/dist/brand/${name}`

function BrandImage({ src, className, alt = '', strokeWidth: _strokeWidth, ...props }) {
  return <img src={asset(src)} className={`brand-product-icon ${className || ''}`} alt={alt} aria-hidden={alt ? undefined : true} {...props} />
}

export function WordPressIcon(props) {
  return <BrandImage src="wordpress.svg" {...props} />
}

export function RedisIcon(props) {
  return <BrandImage src="redis.svg" {...props} />
}

export function NodeIcon(props) {
  return <BrandImage src="nodejs.svg" {...props} />
}

export function PythonIcon(props) {
  return <BrandImage src="python.svg" {...props} />
}

export function GitIcon(props) {
  return <BrandImage src="git.svg" {...props} />
}

const ROUTE_BRANDS = [
  [/^\/wordpress(?:\?|$)/, WordPressIcon],
  [/^\/redis(?:\?|$)/, RedisIcon],
  [/^\/(?:software\/)?node-apps(?:\?|$)/, NodeIcon],
  [/^\/(?:software\/)?python-apps(?:\?|$)/, PythonIcon],
  [/^\/git(?:\?|$)/, GitIcon],
]

export function getProductBrandIcon(to) {
  const match = ROUTE_BRANDS.find(([pattern]) => pattern.test(to || ''))
  return match?.[1] || null
}

export function ProductBrandIcon({ to, ...props }) {
  const Icon = getProductBrandIcon(to)
  if (!Icon) return null
  return <Icon {...props} />
}
