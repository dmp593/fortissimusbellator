document.addEventListener('DOMContentLoaded', function() {
    // Initialize the map
    const map = L.map('map').setView([39.7054288,-8.8573286], 15); // Coordinates for Leiria, Portugal

    // Add OpenStreetMap tiles
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    // Add a custom marker with a dog emoji
    const dogIcon = L.divIcon({
        className: 'marker-fortissimusbellator',
        html: '🐕',
        iconSize: [40, 40], // Size of the emoji
        iconAnchor: [20, 40] // Anchor point of the icon
    });

    const marker = L.marker([39.7054288,-8.8573286], { icon: dogIcon }).addTo(map);

    // Add a popup to the marker
    marker.bindPopup("We are here! 🐕").openPopup();
})