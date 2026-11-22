# Attribution

barbench's repository holds no photos: each set is a list of files that barbench downloads at run
time and checks against their digests, from its photo mirror (release assets of this repository,
which carry these credits and licences) or from the sources. Every set is listed here, in the same
change that adds it. Licences must be on the test-asset allowlist in [AGENTS.md](AGENTS.md).

| Path | Title | Author | Source | Licence |
| ---- | ----- | ------ | ------ | ------- |
| `barbench/lists/zxing-blackbox.json` (downloaded at run time; not in the repository, but in its photo mirror releases) | ZXing blackbox test photos: `code128-2` (40 Code 128 photos), `ean13-2` and `upca-2` (80 negatives), with the text of each EAN-13 and UPC-A photo from the `.txt` file beside it | ZXing authors | [zxing/zxing](https://github.com/zxing/zxing) at commit `1eb71d2557ffdf64c7a7af1a5464489d294e825a`, `core/src/test/resources/blackbox` | Apache-2.0 |
| `barbench/lists/commons-code128.json` (downloaded at run time; not in the repository, but in its photo mirror releases) | 13 photos and scans of Code 128 labels from Wikimedia Commons, each under its own licence, listed below | See below | [Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:Code_128), each file from its pinned original | CC-BY-4.0 AND CC0-1.0 AND LicenseRef-Public-Domain |
| `barbench/lists/kaist-real.json` (downloaded at run time; not in the repository, but in its photo mirror releases) | Barcode Dataset from Quick Browser and Smart Inference papers: 524 photos from `real.zip`, `real/img` (only baseline JPEGs named by 13 digits; the crops of the WWU Muenster and ArTe-Lab sets are left out) | Duy-Thao Do (KAIST) | [Zenodo record 13586402](https://zenodo.org/records/13586402), `real.zip`, SHA-256 `11d04e7cee06024e8b6d2f89e0136bdccc69554061941dcfa552243fabe68538` | CC-BY-4.0 |
| `barbench/lists/maintainer-photos.json` (downloaded at run time; not in the repository, but in its photo mirror releases) | 14 phone photos of barcodes on everyday things, cropped round the symbols, with their metadata removed | A Streepjes maintainer | [barbench/photos](https://github.com/barbench/photos) at commit `3e61df03ec923309f89caff8b70bcf8c44fb8af0`, `maintainer-photos` | CC0-1.0 |
| `barbench/lists/artelab-medium.json` (downloaded at run time; not in the repository, but in its photo mirror releases) | Medium Barcode 1D Collection: all 345 photos | ArTe-Lab, University of Insubria (A. Zamberletti, I. Gallo, M. Carullo, E. Binaghi) | [ArTe-Lab downloads](https://artelab.dista.uninsubria.it/downloads/datasets/barcode/medium_barcode_1d/medium_barcode_1d.html), `medium_barcode_1d.zip` | CC-BY-3.0 |

## Wikimedia Commons photos

The photos in `barbench/lists/commons-code128.json`, by the name barbench caches each under. For the public-domain
files, the licence tag on the Commons file page is given too.

| File | Title | Author | Source | Licence |
| ---- | ----- | ------ | ------ | ------- |
| `swiss-post-ri476394652ch.jpg` | Swiss Post registration label RI476394652CH (scan) | Schweizerische Post | [File:9494-RI476394652CH.jpg](https://commons.wikimedia.org/wiki/File:9494-RI476394652CH.jpg) | LicenseRef-Public-Domain (Commons: {{PD-Switzerland-official}}) |
| `deutsche-post-rk660020635de.jpg` | Deutsche Post registration label, 2011 (scan) | Mattes | [File:Einschreiben-Label Deutsche Post 2011.jpg](https://commons.wikimedia.org/wiki/File:Einschreiben-Label_Deutsche_Post_2011.jpg) | LicenseRef-Public-Domain (Commons: {{PD-ineligible}}) |
| `flagtag-sscc.jpg` | Pallet label with an SSCC in GS1-128 | Usarobert | [File:FlagTag.jpg](https://commons.wikimedia.org/wiki/File:FlagTag.jpg) | LicenseRef-Public-Domain (Commons: {{PD-self}}) |
| `jordan-post-rr008218834jo.jpg` | Jordan Post meter stamp, type PO1B (scan) | Richard Stambaugh | [File:Jordan stamp type PO1B.jpg](https://commons.wikimedia.org/wiki/File:Jordan_stamp_type_PO1B.jpg) | LicenseRef-Public-Domain (Commons: {{PD-self}}) |
| `dhl-parcel-label-2015.jpg` | DHL parcel label, 2015 (scan; the Code 128 symbol is `(96)991001`) | DHL; scan by Raimond Spekking | [File:Paketaufkleber DHL - Produktionsbasierte Abrechnung 2015.jpg](https://commons.wikimedia.org/wiki/File:Paketaufkleber_DHL_-_Produktionsbasierte_Abrechnung_2015.jpg) | LicenseRef-Public-Domain (Commons: {{PD-ineligible}}) |
| `popondetta-price-label.jpg` | Price label of a model-railway shop | E217 | [File:Popondetta PriceLabel.JPG](https://commons.wikimedia.org/wiki/File:Popondetta_PriceLabel.JPG) | CC0-1.0 |
| `nara-shelf-label.jpg` | Shelf label, National Archives Building, Washington | Sarah Stierch (Missvain) | [File:WikiXDC National Archives Barcodes - Stierch.jpg](https://commons.wikimedia.org/wiki/File:WikiXDC_National_Archives_Barcodes_-_Stierch.jpg) | LicenseRef-Public-Domain (Commons: {{PD-user-en}}) |
| `szdc-toilet-receipt.jpg` | Toilet receipt from a Správa železnic ticket machine | Správa železnic; photographed by ŠJů | [File:Poplatek za WC SŽDC.jpg](https://commons.wikimedia.org/wiki/File:Poplatek_za_WC_S%C5%BDDC.jpg) | LicenseRef-Public-Domain (Commons: {{PD-text}}) |
| `rapoo-v500-label.jpg` | Rapoo V500 keyboard back label | zts | [File:Rapoo V500 backplate.jpeg](https://commons.wikimedia.org/wiki/File:Rapoo_V500_backplate.jpeg) | LicenseRef-Public-Domain (Commons: {{PD-author}}) |
| `deposit-voucher.jpg` | Bottle-deposit vouchers (scan; the Code 128 one is on the left) | Sarang | [File:PfandBon55ct.jpg](https://commons.wikimedia.org/wiki/File:PfandBon55ct.jpg) | LicenseRef-Public-Domain (Commons: {{PD-ineligible}}) |
| `zlin-lamp-0360202.jpg` | Street-lamp tag, Zlín (01) | ŠJů, Wikimedia Commons | [File:Zlín, sad Svobody, číslo lampy (01).jpg](https://commons.wikimedia.org/wiki/File:Zl%C3%ADn%2C_sad_Svobody%2C_%C4%8D%C3%ADslo_lampy_%2801%29.jpg) | CC-BY-4.0 |
| `zlin-lamp-0360207.jpg` | Street-lamp tag, Zlín (02) | ŠJů, Wikimedia Commons | [File:Zlín, sad Svobody, číslo lampy (02).jpg](https://commons.wikimedia.org/wiki/File:Zl%C3%ADn%2C_sad_Svobody%2C_%C4%8D%C3%ADslo_lampy_%2802%29.jpg) | CC-BY-4.0 |
| `zlin-lamp-0360220.jpg` | Street-lamp tag, Zlín | ŠJů, Wikimedia Commons | [File:Zlín, sad Svobody, číslo lampy.jpg](https://commons.wikimedia.org/wiki/File:Zl%C3%ADn%2C_sad_Svobody%2C_%C4%8D%C3%ADslo_lampy.jpg) | CC-BY-4.0 |
