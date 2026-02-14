// -------------------------------------------------------------------------------------------------
//  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
//  https://nautechsystems.io
//
//  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
//  You may not use this file except in compliance with the License.
//  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// -------------------------------------------------------------------------------------------------

//! Python bindings for the Rust `FeatherWriter` as `StreamingFeatherWriterV2`.

use std::{
    cell::RefCell,
    collections::{HashMap, HashSet},
    rc::Rc,
};

use nautilus_common::{
    live::get_runtime,
    msgbus::typed_handler::ShareableMessageHandler,
    python::{cache::PyCache, clock::PyClock},
};
use nautilus_core::UnixNanos;
use nautilus_model::data::{
    Bar, Data, IndexPriceUpdate, MarkPriceUpdate, OrderBookDelta, OrderBookDepth10, QuoteTick,
    TradeTick, close::InstrumentClose,
};
use pyo3::{exceptions::PyIOError, prelude::*};

use crate::{
    backend::feather::{FeatherWriter, RotationConfig},
    parquet::create_object_store_from_path,
};

/// Python binding for the Rust `FeatherWriter`.
///
/// This provides a streaming writer of Nautilus objects into feather files with rotation
/// capabilities, matching the interface of Python's `StreamingFeatherWriter`.
#[pyclass(module = "nautilus_trader.core.nautilus_pyo3.persistence", unsendable)]
pub struct StreamingFeatherWriterV2 {
    writer: Rc<RefCell<FeatherWriter>>,
    handler: Option<ShareableMessageHandler>,
}

#[pymethods]
impl StreamingFeatherWriterV2 {
    /// Creates a new `StreamingFeatherWriterV2` instance.
    ///
    /// # Parameters
    ///
    /// - `path`: The path to persist the stream to. Must be a directory.
    /// - `cache`: The cache for query info (PyCache).
    /// - `clock`: The clock to use for time-related operations (PyClock).
    /// - `fs_protocol`: Optional filesystem protocol (default: "file").
    /// - `fs_storage_options`: Optional storage options for cloud backends.
    /// - `include_types`: Optional list of type names to include (e.g., ["quotes", "trades"]).
    /// - `rotation_mode`: Rotation mode (0=SIZE, 1=INTERVAL, 2=SCHEDULED_DATES, 3=NO_ROTATION).
    /// - `max_file_size`: Maximum file size in bytes before rotation (for SIZE mode).
    /// - `rotation_interval_ns`: Rotation interval in nanoseconds (for INTERVAL/SCHEDULED_DATES modes).
    /// - `rotation_time_ns`: Scheduled rotation time in nanoseconds (for SCHEDULED_DATES mode).
    /// - `flush_interval_ms`: Flush interval in milliseconds (default: 1000). Set to 0 to disable auto-flush.
    /// - `replace`: If existing files at the given path should be replaced (default: False).
    #[new]
    #[pyo3(signature = (
        path,
        cache,
        clock,
        fs_protocol=None,
        fs_storage_options=None,
        include_types=None,
        rotation_mode=3,
        max_file_size=1024*1024*1024,
        rotation_interval_ns=None,
        rotation_time_ns=None,
        rotation_timezone="UTC",
        flush_interval_ms=None,
        replace=false
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        path: String,
        cache: PyCache,
        clock: PyClock,
        fs_protocol: Option<String>,
        fs_storage_options: Option<HashMap<String, String>>,
        include_types: Option<Vec<String>>,
        rotation_mode: u8,
        max_file_size: u64,
        rotation_interval_ns: Option<u64>,
        rotation_time_ns: Option<u64>,
        rotation_timezone: &str,
        flush_interval_ms: Option<u64>,
        replace: bool,
    ) -> PyResult<Self> {
        // Create object store from path
        // Use fs_protocol to construct the full path if it's a cloud protocol
        let full_path = if let Some(protocol) = &fs_protocol {
            if protocol != "file" && !path.contains("://") {
                format!("{protocol}://{path}")
            } else {
                path.clone()
            }
        } else {
            path.clone()
        };

        let storage_options = fs_storage_options
            .map(|map| map.into_iter().collect::<ahash::AHashMap<String, String>>());

        let (object_store, _base_path, _original_uri) =
            create_object_store_from_path(&full_path, storage_options)
                .map_err(|e| PyIOError::new_err(format!("Failed to create object store: {e}")))?;

        // Handle replace parameter - delete existing files if requested
        if replace {
            // Note: In Rust, we'd need to list and delete files from the object store
            // For now, this is a placeholder - the actual deletion would need to be implemented
            // based on the object store type (local vs cloud)
        }

        // Convert rotation mode to RotationConfig
        // Python RotationMode: 0=SIZE, 1=INTERVAL, 2=SCHEDULED_DATES, 3=NO_ROTATION
        let rotation_config = match rotation_mode {
            0 => RotationConfig::Size {
                max_size: max_file_size,
            },
            1 => {
                let interval = rotation_interval_ns.unwrap_or(86_400_000_000_000); // Default 1 day
                RotationConfig::Interval {
                    interval_ns: interval,
                }
            }
            2 => {
                let interval = rotation_interval_ns.unwrap_or(86_400_000_000_000); // Default 1 day
                let tz = rotation_timezone.parse::<chrono_tz::Tz>().map_err(|e| {
                    PyIOError::new_err(format!("Failed to parse rotation_timezone: {e}"))
                })?;
                let time_ns = rotation_time_ns.unwrap_or(0);
                RotationConfig::ScheduledDates {
                    interval_ns: interval,
                    rotation_time: UnixNanos::from(time_ns),
                    rotation_timezone: tz,
                }
            }
            3 => RotationConfig::NoRotation,
            _ => RotationConfig::NoRotation, // Default to no rotation for invalid values
        };

        // Convert include_types to HashSet
        let included_types =
            include_types.map(|types| types.into_iter().collect::<HashSet<String>>());

        // Set up per-instrument types (matching Python's _per_instrument_writers)
        let mut per_instrument_types = HashSet::new();
        per_instrument_types.insert("bars".to_string());
        per_instrument_types.insert("order_book_deltas".to_string());
        per_instrument_types.insert("order_book_depths".to_string());
        per_instrument_types.insert("quotes".to_string());
        per_instrument_types.insert("trades".to_string());

        // Extract Clock from Python wrapper
        // PyClock wraps Rc<RefCell<dyn Clock>>, we get the inner Rc
        let clock_rc = clock.clock_rc();
        // Note: Cache parameter is kept for API compatibility with Python StreamingFeatherWriter
        // but is not directly used by FeatherWriter
        let _cache = cache;

        // Create FeatherWriter
        let writer = FeatherWriter::new(
            path,
            object_store,
            clock_rc,
            rotation_config,
            included_types,
            Some(per_instrument_types),
            flush_interval_ms, // Auto-flush interval in milliseconds
        );

        Ok(Self {
            writer: Rc::new(RefCell::new(writer)),
            handler: None,
        })
    }

    /// Subscribes to all messages on the message bus (pattern "*").
    ///
    /// This matches the behavior of Python's StreamingFeatherWriter when subscribed
    /// via `trader.subscribe("*", writer.write)`.
    pub fn subscribe(&mut self) -> PyResult<()> {
        if self.handler.is_some() {
            // Already subscribed
            return Ok(());
        }

        let handler = FeatherWriter::subscribe_to_message_bus(self.writer.clone())
            .map_err(|e| PyIOError::new_err(format!("Failed to subscribe to message bus: {e}")))?;

        self.handler = Some(handler);
        Ok(())
    }

    /// Unsubscribes from the message bus.
    pub fn unsubscribe(&mut self) -> PyResult<()> {
        if let Some(handler) = self.handler.take() {
            FeatherWriter::unsubscribe_from_message_bus(handler);
        }
        Ok(())
    }

    /// Writes a data object to the stream.
    ///
    /// # Parameters
    ///
    /// - `data`: The data object to write (must be a Nautilus data type from pyo3).
    pub fn write(&self, py: Python, data: Py<PyAny>) -> PyResult<()> {
        // Convert PyObject to Data enum
        // This is a simplified approach - in practice, you'd want to check the actual type
        // and convert appropriately. For now, we'll try to extract the data from common pyo3 types.

        // Try to convert from common pyo3 data types
        let data_enum: Data = if let Ok(quote) = data.extract::<QuoteTick>(py) {
            Data::Quote(quote)
        } else if let Ok(trade) = data.extract::<TradeTick>(py) {
            Data::Trade(trade)
        } else if let Ok(bar) = data.extract::<Bar>(py) {
            Data::Bar(bar)
        } else if let Ok(delta) = data.extract::<OrderBookDelta>(py) {
            Data::Delta(delta)
        } else if let Ok(depth) = data.extract::<OrderBookDepth10>(py) {
            Data::Depth10(Box::new(depth))
        } else if let Ok(price) = data.extract::<IndexPriceUpdate>(py) {
            Data::IndexPriceUpdate(price)
        } else if let Ok(price) = data.extract::<MarkPriceUpdate>(py) {
            Data::MarkPriceUpdate(price)
        } else if let Ok(close) = data.extract::<InstrumentClose>(py) {
            Data::InstrumentClose(close)
        } else {
            return Err(PyIOError::new_err(
                "Unsupported data type. Must be one of: QuoteTick, TradeTick, Bar, OrderBookDelta, OrderBookDepth10, IndexPriceUpdate, MarkPriceUpdate, InstrumentClose",
            ));
        };

        // Write data using backend method
        let mut writer = self.writer.borrow_mut();
        let runtime = get_runtime();

        runtime
            .block_on(async { writer.write_data(data_enum).await })
            .map_err(|e| PyIOError::new_err(format!("Failed to write data: {e}")))
    }

    /// Flushes all active buffers by writing any remaining buffered bytes to the object store.
    ///
    /// This is called automatically based on `flush_interval_ms` if configured, but can also
    /// be called manually by the client.
    pub fn flush(&self) -> PyResult<()> {
        let mut writer = self.writer.borrow_mut();
        let runtime = get_runtime();

        runtime
            .block_on(async { writer.flush().await })
            .map_err(|e| PyIOError::new_err(format!("Failed to flush: {e}")))
    }

    /// Closes all writers by flushing and removing them.
    ///
    /// After calling this, no further writes should be performed.
    pub fn close(&self) -> PyResult<()> {
        let mut writer = self.writer.borrow_mut();
        let runtime = get_runtime();

        runtime
            .block_on(async { writer.close().await })
            .map_err(|e| PyIOError::new_err(format!("Failed to close: {e}")))
    }
}
