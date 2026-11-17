use crate::core::{Object, ObjectHash};
use crate::crypto::encryption::EncryptionManager;
use crate::errors::LitError;
use byteorder::{BigEndian, ReadBytesExt, WriteBytesExt};
use flate2::{bufread::ZlibDecoder, write::ZlibEncoder, Compression};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{self, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

const PACK_MAGIC: &[u8; 4] = b"LITP";
const INDEX_MAGIC: &[u8; 4] = b"LITI";
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Format {
    Plain = 1,
    Encrypted = 2,
}
const HEADER_SIZE: u64 = 12;
const ENTRY_HEADER_SIZE: u64 = 17;
const HASH_SIZE: usize = 192;
const INDEX_ENTRY_SIZE: u64 = 4 + HASH_SIZE as u64 + 8 + 4;

#[derive(Debug, Clone)]
pub struct PackEntry {
    pub obj_type: u8,
    pub size: u64,
    pub data: Vec<u8>,
    pub crc32: u32,
}

#[derive(Debug, Clone)]
pub struct PackIndexEntry {
    pub hash: ObjectHash,
    pub offset: u64,
    pub crc32: u32,
}

#[derive(Clone)]
pub(crate) struct Location {
    pub(crate) path: Arc<PathBuf>,
    offset: u64,
    end: u64,
    crc32: u32,
    format: Format,
}

impl Location {
    pub(crate) fn read(
        &self,
        hash: &ObjectHash,
        cipher: Option<&EncryptionManager>,
    ) -> Result<Object, String> {
        if self.format == Format::Plain && cipher.is_some_and(EncryptionManager::is_enabled) {
            return Err(
                "Plaintext v1 pack needs migration. Run 'lit rotate-key' to encrypt it.".into(),
            );
        }
        self.read_raw(hash, cipher).map(|(object, _)| object)
    }

    fn read_raw(
        &self,
        hash: &ObjectHash,
        cipher: Option<&EncryptionManager>,
    ) -> Result<(Object, Vec<u8>), String> {
        let result = (|| {
            let mut file = BufReader::new(open_regular(&self.path)?);
            let file_end = file.get_ref().metadata()?.len();
            if read_header(&mut file, PACK_MAGIC)?.0 != self.format {
                return Err(invalid("Pack and index formats differ"));
            }
            if self.end > file_end {
                return Err(invalid("Pack data truncated"));
            }
            let (object, bytes) = read_entry(
                &mut file,
                self.offset,
                self.end,
                Some(self.crc32),
                self.format,
                cipher,
            )?;
            if ObjectHash::from_bytes(&bytes) != *hash {
                return Err(invalid("Packed content does not match its object ID"));
            }
            Ok((object, bytes))
        })();
        result.map_err(|e: io::Error| {
            format!(
                "Pack {} at offset {}: {e}",
                self.path.display(),
                self.offset
            )
        })
    }
}

#[derive(Default)]
pub(crate) struct Catalog {
    indexes: Vec<PathBuf>,
    pub(crate) objects: HashMap<ObjectHash, Vec<Location>>,
    pub(crate) errors: Vec<String>,
}

impl Catalog {
    pub(crate) fn scan(
        directory: &Path,
        previous: Option<&Arc<Self>>,
    ) -> Result<Arc<Self>, String> {
        let mut indexes = Vec::new();
        match fs::read_dir(directory) {
            Ok(entries) => {
                for entry in entries {
                    let path = entry.map_err(|e| format!("List native packs: {e}"))?.path();
                    if path.extension().is_some_and(|extension| extension == "idx") {
                        indexes.push(path);
                    }
                }
                indexes.sort();
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("List native packs: {e}")),
        }
        if let Some(previous) = previous.filter(|previous| previous.indexes == indexes) {
            return Ok(Arc::clone(previous));
        }
        let mut catalog = Self {
            indexes,
            ..Self::default()
        };
        // The index is the publication marker. An unindexed pack can be left
        // behind by a process stopped before publication.
        for index in &catalog.indexes {
            match indexed_objects(index) {
                Ok(entries) => {
                    for (hash, location) in entries {
                        catalog.objects.entry(hash).or_default().push(location);
                    }
                }
                Err(error) => catalog.errors.push(error),
            }
        }
        Ok(Arc::new(catalog))
    }
}

pub(crate) fn indexed_objects(index_path: &Path) -> Result<Vec<(ObjectHash, Location)>, String> {
    parse_index(index_path).map_err(|e| format!("Pack index {}: {e}", index_path.display()))
}

fn parse_index(index_path: &Path) -> io::Result<Vec<(ObjectHash, Location)>> {
    let index_file = open_regular(index_path)?;
    let index_size = index_file.metadata()?.len();
    let mut index = BufReader::new(index_file);
    let (format, count) = read_header(&mut index, INDEX_MAGIC)?;
    if index_size != HEADER_SIZE + u64::from(count) * INDEX_ENTRY_SIZE {
        return Err(invalid("Index length does not match its entry count"));
    }

    let pack_path = Arc::new(index_path.with_extension("pack"));
    let pack = open_regular(&pack_path)?;
    let pack_size = pack.metadata()?.len();
    let mut pack = BufReader::new(pack);
    let (pack_format, pack_count) = read_header(&mut pack, PACK_MAGIC)?;
    if pack_format != format {
        return Err(invalid("Pack and index formats differ"));
    }
    if pack_count != count {
        return Err(invalid("Pack and index entry counts differ"));
    }

    let mut entries = Vec::new();
    for _ in 0..count {
        if index.read_u32::<BigEndian>()? != HASH_SIZE as u32 {
            return Err(invalid("Invalid pack object ID length"));
        }
        let mut hash = [0u8; HASH_SIZE];
        index.read_exact(&mut hash)?;
        let hash = std::str::from_utf8(&hash).map_err(invalid)?;
        if !ObjectHash::is_full_hex(hash) {
            return Err(invalid("Invalid pack object ID"));
        }
        if entries
            .last()
            .is_some_and(|(previous, _): &(ObjectHash, Location)| previous.as_str() >= hash)
        {
            return Err(invalid("Pack index object IDs must be sorted and unique"));
        }
        let offset = index.read_u64::<BigEndian>()?;
        let crc32 = index.read_u32::<BigEndian>()?;
        let location = Location {
            path: pack_path.clone(),
            offset,
            end: pack_size,
            crc32,
            format,
        };
        entries.push((ObjectHash::from_hex(hash.into()), location));
    }

    // Derive each entry's exclusive end from the next physical offset.
    let mut positions: Vec<_> = (0..entries.len()).collect();
    positions.sort_unstable_by_key(|&i| entries[i].1.offset);
    let start = positions
        .first()
        .map_or(pack_size, |&i| entries[i].1.offset);
    if start != HEADER_SIZE {
        return Err(invalid("Pack entries do not start after its header"));
    }
    for (position, &i) in positions.iter().enumerate() {
        let end = positions
            .get(position + 1)
            .map_or(pack_size, |&j| entries[j].1.offset);
        let location = &mut entries[i].1;
        if end > pack_size || end.saturating_sub(location.offset) <= ENTRY_HEADER_SIZE {
            return Err(invalid("Invalid or overlapping pack offsets"));
        }
        location.end = end;
    }
    Ok(entries)
}

fn open_regular(path: &Path) -> io::Result<File> {
    if !fs::symlink_metadata(path)?.is_file() {
        return Err(invalid(format!(
            "Expected a regular file at {}",
            path.display()
        )));
    }
    File::open(path)
}

fn read_header(file: &mut impl Read, expected_magic: &[u8; 4]) -> io::Result<(Format, u32)> {
    let mut magic = [0; 4];
    file.read_exact(&mut magic)?;
    if &magic != expected_magic {
        return Err(invalid("Invalid pack or index magic"));
    }
    let format = match file.read_u32::<BigEndian>()? {
        1 => Format::Plain,
        2 => Format::Encrypted,
        _ => return Err(invalid("Unsupported pack or index version")),
    };
    Ok((format, file.read_u32::<BigEndian>()?))
}

fn read_entry(
    file: &mut (impl Read + Seek),
    offset: u64,
    entry_end: u64,
    expected_crc: Option<u32>,
    format: Format,
    cipher: Option<&EncryptionManager>,
) -> io::Result<(Object, Vec<u8>)> {
    if offset < HEADER_SIZE || entry_end.saturating_sub(offset) <= ENTRY_HEADER_SIZE {
        return Err(invalid("Invalid pack entry bounds"));
    }
    file.seek(SeekFrom::Start(offset))?;
    let kind = file.read_u8()?;
    if !(1..=4).contains(&kind) {
        return Err(invalid("Invalid pack object type"));
    }
    let raw_size = file.read_u64::<BigEndian>()?;
    let size = file.read_u64::<BigEndian>()?;
    let available = entry_end - offset - ENTRY_HEADER_SIZE;
    if size == 0 || size > available || (expected_crc.is_some() && size != available) {
        return Err(invalid("Compressed size disagrees with pack entry bounds"));
    }
    let mut compressed = Vec::new();
    file.take(size).read_to_end(&mut compressed)?;
    if compressed.len() as u64 != size {
        return Err(invalid("Pack data truncated"));
    }
    if expected_crc.is_some_and(|crc| crc32fast::hash(&compressed) != crc) {
        return Err(invalid("Pack entry CRC32 mismatch"));
    }
    let compressed = match format {
        Format::Plain => compressed,
        Format::Encrypted => cipher
            .filter(|cipher| cipher.is_enabled())
            .ok_or_else(|| invalid("Encrypted native pack requires an initialized repository key"))?
            .decrypt(&compressed)
            .map_err(invalid)?,
    };
    let mut decoder = ZlibDecoder::new(compressed.as_slice());
    let mut raw = Vec::new();
    decoder
        .by_ref()
        .take(raw_size.saturating_add(1))
        .read_to_end(&mut raw)?;
    if raw.len() as u64 != raw_size || !decoder.into_inner().is_empty() {
        return Err(invalid(
            "Pack entry decompressed size or stream boundary mismatch",
        ));
    }
    let object = Object::from_bytes(&raw).map_err(invalid)?;
    if object_kind(&object) != kind {
        return Err(invalid("Pack entry type disagrees with its content"));
    }
    Ok((object, raw))
}

fn object_kind(object: &Object) -> u8 {
    match object {
        Object::Blob(_) => 1,
        Object::Tree(_) => 2,
        Object::Commit(_) => 3,
        Object::Tag(_) => 4,
    }
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

pub fn read_pack_object(pack_path: &Path, offset: u64) -> Result<Object, LitError> {
    let result = (|| {
        let file = open_regular(pack_path)?;
        let end = file.metadata()?.len();
        let mut file = BufReader::new(file);
        let (format, _) = read_header(&mut file, PACK_MAGIC)?;
        read_entry(&mut file, offset, end, None, format, None).map(|(object, _)| object)
    })();
    result.map_err(|e| {
        LitError::object(format!(
            "Pack {} at offset {offset}: {e}",
            pack_path.display()
        ))
    })
}

pub fn load_pack_index(index_path: &Path) -> Result<HashMap<String, (PathBuf, u64)>, String> {
    Ok(indexed_objects(index_path)?
        .into_iter()
        .map(|(hash, location)| {
            (
                hash.to_string(),
                ((*location.path).clone(), location.offset),
            )
        })
        .collect())
}

pub fn write_pack(
    objects: &[(ObjectHash, Object)],
    pack_path: &Path,
) -> Result<Vec<PackIndexEntry>, LitError> {
    write_entries(
        pack_path,
        objects.len(),
        Format::Plain,
        Ok,
        objects
            .iter()
            .map(|(hash, object)| Ok((hash.clone(), object_kind(object), object.to_bytes()))),
    )
}

pub(crate) fn write_loose(
    store: &super::ObjectStore,
    hashes: &[ObjectHash],
    pack_path: &Path,
    format: Format,
) -> Result<Vec<PackIndexEntry>, LitError> {
    write_entries(
        pack_path,
        hashes.len(),
        format,
        |compressed| match format {
            Format::Plain => Ok(compressed),
            Format::Encrypted => store.with_cipher(false, |cipher| cipher.encrypt(&compressed)),
        },
        hashes.iter().map(|hash| {
            let raw = store.verified_loose_bytes(hash)?;
            let object = Object::from_bytes(&raw)?;
            Ok((hash.clone(), object_kind(&object), raw))
        }),
    )
}

pub(crate) fn rotate(
    old_index: &Path,
    new_index: &Path,
    old: &EncryptionManager,
    new: &EncryptionManager,
) -> Result<usize, LitError> {
    let entries = indexed_objects(old_index)?;
    let count = entries.len();
    let entries = write_entries(
        &new_index.with_extension("pack"),
        count,
        Format::Encrypted,
        |compressed| new.encrypt(&compressed),
        entries.into_iter().map(|(hash, location)| {
            let (object, raw) = location.read_raw(&hash, Some(old))?;
            Ok((hash, object_kind(&object), raw))
        }),
    )?;
    write_index(&entries, new_index, Format::Encrypted)?;
    for (hash, location) in indexed_objects(new_index)? {
        location.read(&hash, Some(new))?;
    }
    Ok(count)
}

fn write_entries(
    path: &Path,
    count: usize,
    format: Format,
    encode: impl Fn(Vec<u8>) -> Result<Vec<u8>, String>,
    entries: impl Iterator<Item = Result<(ObjectHash, u8, Vec<u8>), String>>,
) -> Result<Vec<PackIndexEntry>, LitError> {
    let mut out = BufWriter::new(File::create(path)?);
    write_header(&mut out, PACK_MAGIC, count, format)?;
    let mut index = Vec::new();
    let mut offset = HEADER_SIZE;
    for entry in entries {
        let (hash, kind, raw) = entry?;
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::best());
        encoder.write_all(&raw)?;
        let data = encode(encoder.finish()?)?;
        index.push(PackIndexEntry {
            hash,
            offset,
            crc32: crc32fast::hash(&data),
        });
        out.write_u8(kind)?;
        out.write_u64::<BigEndian>(raw.len() as u64)?;
        out.write_u64::<BigEndian>(data.len() as u64)?;
        out.write_all(&data)?;
        offset += ENTRY_HEADER_SIZE + data.len() as u64;
    }
    out.flush()?;
    out.get_ref().sync_all()?;
    Ok(index)
}

pub fn write_pack_index(entries: &[PackIndexEntry], index_path: &Path) -> Result<(), LitError> {
    write_index(entries, index_path, Format::Plain)
}

pub(crate) fn write_index(
    entries: &[PackIndexEntry],
    index_path: &Path,
    format: Format,
) -> Result<(), LitError> {
    let mut out = BufWriter::new(File::create(index_path)?);
    write_header(&mut out, INDEX_MAGIC, entries.len(), format)?;
    let mut sorted: Vec<_> = entries.iter().collect();
    sorted.sort_by(|a, b| a.hash.as_str().cmp(b.hash.as_str()));
    for entry in sorted {
        if !ObjectHash::is_full_hex(entry.hash.as_str()) {
            return Err("Invalid pack object ID".into());
        }
        out.write_u32::<BigEndian>(HASH_SIZE as u32)?;
        out.write_all(entry.hash.as_str().as_bytes())?;
        out.write_u64::<BigEndian>(entry.offset)?;
        out.write_u32::<BigEndian>(entry.crc32)?;
    }
    out.flush()?;
    out.get_ref().sync_all()?;
    Ok(())
}

fn write_header(
    out: &mut impl Write,
    magic: &[u8; 4],
    count: usize,
    format: Format,
) -> io::Result<()> {
    out.write_all(magic)?;
    out.write_u32::<BigEndian>(format as u32)?;
    out.write_u32::<BigEndian>(u32::try_from(count).map_err(invalid)?)
}
