from html import escape

def ruler_row(stat, peak, max_level):
    # The three ranges share the same zero and maximum for this card.
    start = 3 + 207 * stat["initial"] / peak
    end = 3 + 207 * stat["max"] / peak
    middle = (start + end) / 2
    labels = (f'<div class="axis-levels" style="--first:{100*start/213:.3f}%;--last:{100*end/213:.3f}%">'
              f'<span>Lv.1</span><span>Lv.{max_level}</span></div>')
    return f'''<tr class="reading"><th scope="row" style="--stat:{stat["color"]}">{escape(stat["label"])}</th><td>{stat["initial"]:,}</td><td>{stat["max"]:,}</td></tr>
      <tr class="rail" aria-hidden="true"><td colspan="3" style="--ink:color-mix(in srgb,{stat["color"]} 72%,#334e5c)">
        <svg viewBox="0 0 213 10" preserveAspectRatio="none">
          <path fill="var(--ink)" d="M{start:.2f} 6.4Q{middle:.2f} 5.7 {end:.2f} 5.9L{end:.2f} 8.2Q{middle:.2f} 7.8 {start:.2f} 7.5Z"/>
          <path class="ink" d="m{start-0.65:.2f} 9.4 1.3-4.8" stroke-width="1.65" stroke-linecap="round"/>
          <circle class="tip" cx="{end:.2f}" cy="7" r="2.2"/>
        </svg>{labels}</td></tr>'''
