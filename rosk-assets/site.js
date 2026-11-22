const lightbox = document.querySelector('.lightbox');
const image = lightbox.querySelector('img');

if (typeof lightbox.showModal === 'function') {
  for (const link of document.querySelectorAll('.screenshot')) {
    link.addEventListener('click', (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;

      event.preventDefault();
      image.src = link.href;
      image.alt = link.querySelector('img').alt;
      lightbox.showModal();
    });
  }

  lightbox.addEventListener('click', (event) => {
    if (event.target === lightbox) lightbox.close();
  });
}

async function updateReleaseDownload() {
  const download = document.querySelector('[data-release-download]');
  const versionLabel = download.querySelector('[data-release-version]');
  const files = document.querySelectorAll('[data-release-file]');

  try {
    const response = await fetch('https://api.github.com/repos/miamibc/rosk/releases/latest');
    if (!response.ok) return;

    const release = await response.json();
    const version = /^v(\d+\.\d+\.\d+)$/.exec(release.tag_name)?.[1];
    if (!version) return;

    const filename = `rosk_${version}_all.deb`;
    const url = `https://github.com/miamibc/rosk/releases/download/${release.tag_name}/${filename}`;
    const hasPackage = release.assets.some((asset) => asset.name === filename && asset.browser_download_url === url);
    if (!hasPackage) return;

    download.href = url;
    versionLabel.textContent = version;
    for (const file of files) file.textContent = filename;
  } catch {
    // The HTML already links to a known release when GitHub's API is unavailable.
  }
}

updateReleaseDownload();
