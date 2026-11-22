//! # OpenLit
//!
//! Version control for people and coding agents. The command is `lit`; the
//! Rust library also keeps the `lit` import name for compatibility.
//!
//! OpenLit provides JSON command responses, JSONL batches with rollback,
//! Git import and export, and an MCP server. This OpenLit version is
//! 0.1.1. It is based on the MIT-licensed Lit snapshot, with independent
//! recovery work. See the repository guides for tested limits.
//!
//! ## Usage
//!
//! ```no_run
//! use lit::commands;
//! use lit::ontology;
//!
//! // Generate the full JSON Schema for agent SDK integration
//! let schema = ontology::generate_schemas();
//!
//! // Get the machine-readable ontology
//! let ont = ontology::get_ontology();
//! ```

/// VCS command implementations (init, add, commit, status, diff, merge, etc.)
pub mod commands;
/// Hierarchical configuration (repo-local → user global → env vars)
pub mod config;
/// Core VCS primitives: objects (blob, tree, commit, tag), refs, and repo discovery
pub mod core;
/// Post-quantum cryptographic operations: hashing, signing, encryption, key management
pub mod crypto;
/// Typed error system with machine-readable error codes and remediation hints
pub mod errors;
/// Output formatting (JSON, human-readable, MessagePack)
pub mod formatter;
/// Network transport layer: HTTPS, SSH, lit:// protocol, air-gap enforcement
pub mod network;
/// Machine-readable ontology and JSON Schema generation for agent discovery
pub mod ontology;
/// Typed response structs for all 42+ CLI commands
pub mod response;
/// Object store and index (staging area) persistence
pub mod storage;

// Re-export CLI types that are used by commands
#[derive(Clone, Debug)]
pub enum RemoteCommands {
    Add { name: String, url: String },
    Remove { name: String },
    List { verbose: bool },
}

#[derive(Clone, Debug)]
pub enum ConfigCommands {
    Show,
    Get { key: String },
    Set { key: String, value: String },
}

#[derive(Clone, Debug)]
pub enum StashCommands {
    Push { message: Option<String> },
    Pop,
    Apply { index: Option<usize> },
    List,
    Drop { index: Option<usize> },
}

#[derive(Clone, Debug)]
pub enum BisectCommands {
    Start,
    Good { commit: String },
    Bad { commit: String },
    Reset,
}

#[derive(Clone, Debug)]
pub enum TxCommands {
    Begin,
    Commit,
    Rollback,
}

#[derive(Clone, Debug)]
pub enum SwarmCommands {
    Register {
        agent_id: String,
    },
    List,
    LeaseAcquire {
        agent_id: String,
        path: String,
        duration: u64,
    },
    LeaseRelease {
        agent_id: String,
        path: String,
    },
    LeaseList,
}

#[derive(Clone, Debug)]
pub enum LfsCommands {
    Track { patterns: Vec<String> },
    Migrate { threshold: Option<u64> },
}
