const asset = (name) => `/static/dist/brand/${name}`

function BrandImage({ src, className, alt = '', strokeWidth: _strokeWidth, ...props }) {
  return <img src={asset(src)} className={className} alt={alt} aria-hidden={alt ? undefined : true} {...props} />
}

export function WordPressIcon(props) {
  return <BrandImage src="wordpress.svg" {...props} />
}

export function RedisIcon(props) {
  return <BrandImage src="redis.svg" {...props} />
}
